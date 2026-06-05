"""Tests for TFactory token-usage instrumentation (#224, RFC-0001 v1.1).

Covers the usage accumulator, the tolerant provider normalizer, the on-disk
accumulation across sessions/handback retries, and the additive ``usage`` block
on the completion envelope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.triager import _build_completion_envelope, _write_status_patch
from usage import (
    RunUsage,
    estimate_cost_usd,
    record_in_status,
    usage_block_from_status,
    usage_from_obj,
)


class _FakeResultMessage:
    """Stand-in for the Claude Agent SDK ResultMessage seen in session.py."""

    def __init__(self, input_tokens, output_tokens, *, model="", total_cost_usd=None):
        self.usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        self.model = model
        if total_cost_usd is not None:
            self.total_cost_usd = total_cost_usd


# --- accumulator + pricing -------------------------------------------------


def test_add_and_event_block():
    u = RunUsage(
        input_tokens=100, output_tokens=20, cost_usd=0.01, model="claude-sonnet-4-6"
    )
    u.add(RunUsage(input_tokens=50, output_tokens=5, cost_usd=0.02))
    assert u.as_event_block() == {
        "input_tokens": 150,
        "output_tokens": 25,
        "total_tokens": 175,
        "cost_usd": 0.03,
        "model": "claude-sonnet-4-6",  # first non-empty model wins
    }


def test_estimate_cost_known_and_unknown_model():
    assert (
        estimate_cost_usd(1_000_000, 1_000_000, "claude-sonnet-4-6") == 18.0
    )  # 3 + 15
    assert estimate_cost_usd(1_000_000, 0, "who-knows") == 0.0  # never guess


# --- normalizer ------------------------------------------------------------


def test_usage_from_sdk_result_message_uses_real_cost():
    msg = _FakeResultMessage(321, 88, model="claude-sonnet-4-6", total_cost_usd=0.5)
    u = usage_from_obj(msg)
    assert (u.input_tokens, u.output_tokens, u.cost_usd, u.model) == (
        321,
        88,
        0.5,
        "claude-sonnet-4-6",
    )


def test_usage_from_result_message_estimates_cost_when_absent():
    msg = _FakeResultMessage(
        1_000_000, 0, model="claude-sonnet-4-6"
    )  # no total_cost_usd
    u = usage_from_obj(msg)
    assert u.cost_usd == 3.0  # 1M input @ $3/Mtok


def test_usage_from_dict_and_none_and_zero():
    assert (
        usage_from_obj(
            {"input_tokens": 10, "output_tokens": 2, "model": "x"}
        ).input_tokens
        == 10
    )
    assert usage_from_obj(None) is None
    assert usage_from_obj(_FakeResultMessage(0, 0)) is None  # no tokens → None


# --- on-disk accumulation (handback retries) -------------------------------


def test_record_in_status_accumulates_across_sessions(tmp_path: Path):
    (tmp_path / "status.json").write_text(json.dumps({"task_id": "042"}))
    record_in_status(
        tmp_path, RunUsage(input_tokens=100, output_tokens=10, cost_usd=0.1, model="m")
    )
    record_in_status(
        tmp_path, RunUsage(input_tokens=50, output_tokens=5, cost_usd=0.2)
    )  # a retry
    block = usage_block_from_status(tmp_path)
    assert block["input_tokens"] == 150
    assert block["output_tokens"] == 15
    assert block["total_tokens"] == 165
    assert block["cost_usd"] == 0.3
    assert block["model"] == "m"
    # status.json kept its other fields
    assert json.loads((tmp_path / "status.json").read_text())["task_id"] == "042"


def test_usage_block_zeros_when_nothing_recorded(tmp_path: Path):
    assert usage_block_from_status(tmp_path) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "model": "",
    }


def test_record_in_status_ignores_empty_usage(tmp_path: Path):
    record_in_status(tmp_path, None)
    record_in_status(tmp_path, RunUsage())  # zero tokens
    assert not (tmp_path / "status.json").exists()


# --- completion envelope ---------------------------------------------------


def test_envelope_carries_zero_usage_by_default(tmp_path: Path):
    (tmp_path / "status.json").write_text(
        json.dumps({"task_id": "042", "status": "triaged"})
    )
    env = _build_completion_envelope(tmp_path, {"task_id": "042", "status": "triaged"})
    assert env["usage"]["total_tokens"] == 0
    assert env["schema_version"] == "1.1"


def test_envelope_reflects_recorded_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    (tmp_path / "findings").mkdir(parents=True, exist_ok=True)
    (tmp_path / "status.json").write_text(
        json.dumps({"task_id": "042", "status": "triaging"})
    )
    record_in_status(
        tmp_path,
        RunUsage(
            input_tokens=2400,
            output_tokens=100,
            cost_usd=1.25,
            model="claude-sonnet-4-6",
        ),
    )

    _write_status_patch(tmp_path, status="triaged")  # terminal → writes COMPLETED.json
    env = json.loads((tmp_path / "findings" / "COMPLETED.json").read_text())
    assert env["usage"] == {
        "input_tokens": 2400,
        "output_tokens": 100,
        "total_tokens": 2500,
        "cost_usd": 1.25,
        "model": "claude-sonnet-4-6",
    }
