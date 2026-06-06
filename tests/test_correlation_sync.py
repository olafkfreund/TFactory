"""Tests for RFC-0001 correlation sync: completion event + handback (#249)."""

from __future__ import annotations

import json

import pytest
from agents.handback import build_correction_request
from agents.handback.send import _correlation_key_for, send_correction
from agents.triager import _correlation_key

_SOURCE = {
    "aifactory": {
        "project_id": "demo",
        "spec_id": "001-login",
        "api_url": "http://localhost:3101",
        "task_id": "demo:001-login",
    }
}
_VERDICTS = {"verdicts": [{"test_id": "t_bad", "verdict": "reject", "reasons": ["x"], "lane": "api"}]}
_TRIAGE = {"rejected": [{"test_id": "t_bad", "test_file": "tests/test_x.py"}]}


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "context").mkdir()
    (tmp_path / "findings").mkdir()
    return tmp_path


def _write_contract(spec, key):
    (spec / "context" / "aifactory_plan.json").write_text(
        json.dumps({"contract_version": "2", "correlation_key": key, "tfactory": {"lanes": ["unit"]}})
    )


# ─── triager _correlation_key precedence ─────────────────────────────────


def test_correlation_prefers_contract_key(spec):
    _write_contract(spec, "PF-123")
    # even with a competing issue number in status, the contract wins
    assert _correlation_key(spec, {"issue_number": 9}, {}) == "PF-123"


def test_correlation_falls_back_to_issue_number(spec):
    assert _correlation_key(spec, {"issue_number": 42}, {}) == "42"


def test_correlation_synthetic_fallback(spec):
    key = _correlation_key(spec, {"spec_id": "001-login"}, {})
    assert key == "tf-001-login"


# ─── handback correlation_key_for ────────────────────────────────────────


def test_handback_key_from_contract(spec):
    _write_contract(spec, "PF-777")
    assert _correlation_key_for(spec) == "PF-777"


def test_handback_key_from_source_issue(spec):
    (spec / "context" / "source.json").write_text(json.dumps({"issue_number": 55}))
    assert _correlation_key_for(spec) == "55"


def test_handback_key_none_when_absent(spec):
    assert _correlation_key_for(spec) is None


# ─── handback payload carries correlation_key end-to-end ─────────────────


def test_handback_send_includes_correlation_key(spec):
    _write_contract(spec, "PF-9")
    captured = {}
    res = send_correction(
        build_correction_request(_VERDICTS, _TRIAGE, _SOURCE),
        spec,
        dry_run=False,
        confirm=True,
        sender_fn=lambda payload: captured.update(payload) or {"ok": True},
    )
    assert res.sent is True
    assert captured["correlation_key"] == "PF-9"
    # artifact also records it
    doc = json.loads((spec / "findings" / "handback_request.json").read_text())
    assert doc["correlation_key"] == "PF-9"


def test_handback_artifact_records_key_in_dry_run(spec):
    _write_contract(spec, "PF-5")
    send_correction(
        build_correction_request(_VERDICTS, _TRIAGE, _SOURCE), spec, dry_run=True
    )
    doc = json.loads((spec / "findings" / "handback_request.json").read_text())
    assert doc["correlation_key"] == "PF-5"
