#!/usr/bin/env python3
"""Tests for the Triager's quality-gate status wiring (WS1 next slice).

Covers `_load_quality_gate_policy` (reads the snapshotted .tfactory.yml block)
and `_run_pr_status_side_effect` (enabled/disabled, missing sha/repo, missing
verdicts, dry-run argv, and the TFACTORY_PR_STATUS toggle). No subprocess —
post_pr_status is exercised in its own dry-run path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.triager import (  # noqa: E402
    _load_quality_gate_policy,
    _pr_status_dry_run,
    _run_pr_status_side_effect,
)


def _spec_with(tmp_path: Path, *, quality_gate=None, verdicts=None) -> Path:
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "findings").mkdir(parents=True)
    cfg = {"version": 1, "targets": []}
    if quality_gate is not None:
        cfg["quality_gate"] = quality_gate
    (spec / "context" / "tfactory_yml.json").write_text(json.dumps(cfg))
    if verdicts is not None:
        (spec / "findings" / "verdicts.json").write_text(
            json.dumps({"verdicts": verdicts})
        )
    return spec


def _accept(test_id="a") -> dict:
    return {
        "test_id": test_id,
        "verdict": "accept",
        "signals_summary": {"stability": "stable", "mutation": "killed", "ci_parity": "yes"},
    }


# ─── policy loading ───────────────────────────────────────────────────────


def test_policy_default_when_no_config(tmp_path):
    spec = tmp_path / "empty"
    (spec / "context").mkdir(parents=True)
    policy = _load_quality_gate_policy(spec)
    assert policy.enabled is False


def test_policy_loaded_from_block(tmp_path):
    spec = _spec_with(tmp_path, quality_gate={"enabled": True, "min_accept_rate": 0.5})
    policy = _load_quality_gate_policy(spec)
    assert policy.enabled is True and policy.min_accept_rate == 0.5


def test_policy_default_on_malformed_json(tmp_path):
    spec = tmp_path / "bad"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "tfactory_yml.json").write_text("{not json")
    assert _load_quality_gate_policy(spec).enabled is False


# ─── side-effect: skip paths ──────────────────────────────────────────────


def test_skips_when_gate_disabled(tmp_path):
    spec = _spec_with(tmp_path, quality_gate={"enabled": False}, verdicts=[_accept()])
    out = _run_pr_status_side_effect(tmp_path, spec / "findings", {"sha": "x", "repo_slug": "a/b"}, spec)
    assert out["skipped"] and "not enabled" in out["reason"]


def test_skips_when_no_sha_or_repo(tmp_path):
    spec = _spec_with(tmp_path, quality_gate={"enabled": True}, verdicts=[_accept()])
    out = _run_pr_status_side_effect(tmp_path, spec / "findings", {}, spec)
    assert out["skipped"] and "no sha/repo" in out["reason"]


def test_skips_when_verdicts_missing(tmp_path):
    spec = _spec_with(tmp_path, quality_gate={"enabled": True}, verdicts=None)
    out = _run_pr_status_side_effect(
        tmp_path, spec / "findings", {"sha": "abc", "repo_slug": "a/b"}, spec
    )
    assert out["skipped"] and "not evaluated" in out["reason"]


# ─── side-effect: active path ─────────────────────────────────────────────


def test_posts_dry_run_when_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("TFACTORY_PR_STATUS", raising=False)  # default → dry-run
    spec = _spec_with(tmp_path, quality_gate={"enabled": True}, verdicts=[_accept()])
    out = _run_pr_status_side_effect(
        tmp_path, spec / "findings", {"sha": "abc", "repo_slug": "acme/w", "pr_number": 5}, spec
    )
    assert out["skipped"] is False
    assert out["passed"] is True and out["state"] == "success"
    assert out["dry_run"] is True and out["ok"] is True
    # argv targets the right statuses endpoint + carries the PR target_url
    assert "repos/acme/w/statuses/abc" in out["argv"]
    assert any(a == "target_url=https://github.com/acme/w/pull/5" for a in out["argv"])


def test_failing_gate_reports_failure_state(tmp_path):
    # enabled + a survived-mutation accept → gate fails
    bad = {
        "test_id": "a",
        "verdict": "accept",
        "signals_summary": {"stability": "stable", "mutation": "survived", "ci_parity": "yes"},
    }
    spec = _spec_with(tmp_path, quality_gate={"enabled": True}, verdicts=[bad])
    out = _run_pr_status_side_effect(
        tmp_path, spec / "findings", {"sha": "abc", "repo_slug": "a/b"}, spec
    )
    assert out["passed"] is False and out["state"] == "failure"
    assert "state=failure" in out["argv"]


def test_dry_run_toggle_reads_env(monkeypatch):
    # Pure env check — avoids invoking real `gh`. Default = dry-run.
    monkeypatch.delenv("TFACTORY_PR_STATUS", raising=False)
    assert _pr_status_dry_run() is True
    monkeypatch.setenv("TFACTORY_PR_STATUS", "1")
    assert _pr_status_dry_run() is False
    monkeypatch.setenv("TFACTORY_PR_STATUS", "0")
    assert _pr_status_dry_run() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
