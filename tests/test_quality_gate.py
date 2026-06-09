#!/usr/bin/env python3
"""Tests for the PR quality gate (WS1) — agents/quality_gate.py.

Covers count/rate thresholds, per-accepted-test signal guardrails (survived
mutation / mocked subject / instability), the empty-suite case, and policy
construction from a ``.tfactory.yml`` mapping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.quality_gate import (  # noqa: E402
    STATE_FAILURE,
    STATE_SUCCESS,
    GatePolicy,
    GateResult,
    evaluate_gate,
)


def _write_verdicts(tmp_path: Path, verdicts: list[dict]) -> Path:
    p = tmp_path / "verdicts.json"
    p.write_text(json.dumps({"verdicts": verdicts}))
    return p


def _accept(test_id: str, **sig) -> dict:
    base = {"stability": "stable", "mutation": "killed", "ci_parity": "yes"}
    base.update(sig)
    return {"test_id": test_id, "verdict": "accept", "signals_summary": base}


# ─── happy path ─────────────────────────────────────────────────────────


def test_passes_with_clean_accepts(tmp_path):
    path = _write_verdicts(tmp_path, [_accept("a"), _accept("b")])
    result = evaluate_gate(path, GatePolicy(enabled=True))
    assert result.passed is True
    assert result.state == STATE_SUCCESS
    assert result.counts == {"accept": 2, "flag": 0, "reject": 0, "total": 2}
    assert "passed" in result.summary


def test_summary_within_github_limit(tmp_path):
    path = _write_verdicts(tmp_path, [_accept("a")])
    result = evaluate_gate(path, GatePolicy())
    assert len(result.summary) <= 140


# ─── count / rate thresholds ──────────────────────────────────────────────


def test_empty_suite_fails_when_min_accepted_positive(tmp_path):
    path = _write_verdicts(tmp_path, [])
    result = evaluate_gate(path, GatePolicy(min_accepted=1))
    assert result.passed is False
    assert any("no tests" in r for r in result.reasons)


def test_empty_suite_passes_when_min_accepted_zero(tmp_path):
    path = _write_verdicts(tmp_path, [])
    result = evaluate_gate(path, GatePolicy(min_accepted=0))
    assert result.passed is True


def test_min_accept_rate_enforced(tmp_path):
    # 1 accept / 3 total = 33% < required 50%
    verdicts = [
        _accept("a"),
        {"test_id": "b", "verdict": "flag", "signals_summary": {}},
        {"test_id": "c", "verdict": "flag", "signals_summary": {}},
    ]
    path = _write_verdicts(tmp_path, verdicts)
    result = evaluate_gate(path, GatePolicy(min_accept_rate=0.5))
    assert result.passed is False
    assert any("accept-rate" in r for r in result.reasons)


def test_max_flag_rate_enforced(tmp_path):
    verdicts = [_accept("a")] + [
        {"test_id": f"f{i}", "verdict": "flag", "signals_summary": {}} for i in range(3)
    ]
    path = _write_verdicts(tmp_path, verdicts)
    result = evaluate_gate(path, GatePolicy(max_flag_rate=0.5))
    assert result.passed is False
    assert any("flag-rate" in r for r in result.reasons)


def test_block_on_reject(tmp_path):
    verdicts = [_accept("a"), {"test_id": "b", "verdict": "reject", "signals_summary": {}}]
    path = _write_verdicts(tmp_path, verdicts)
    assert evaluate_gate(path, GatePolicy(block_on_reject=True)).passed is False
    # Default policy does NOT block on reject (rejects are dropped junk tests).
    assert evaluate_gate(path, GatePolicy(block_on_reject=False)).passed is True


# ─── per-accepted-test signal guardrails ──────────────────────────────────


def test_accepted_survived_mutation_fails(tmp_path):
    path = _write_verdicts(tmp_path, [_accept("a", mutation="survived")])
    result = evaluate_gate(path, GatePolicy())
    assert result.passed is False
    assert any("survived its mutation" in r for r in result.reasons)


def test_accepted_mocked_subject_fails(tmp_path):
    path = _write_verdicts(tmp_path, [_accept("a", ci_parity="mocked-subject")])
    result = evaluate_gate(path, GatePolicy())
    assert result.passed is False
    assert any("mocks its subject" in r for r in result.reasons)


def test_accepted_unstable_fails(tmp_path):
    path = _write_verdicts(tmp_path, [_accept("a", stability="flaky")])
    result = evaluate_gate(path, GatePolicy())
    assert result.passed is False
    assert any("not stable" in r for r in result.reasons)


def test_guardrails_can_be_disabled(tmp_path):
    path = _write_verdicts(tmp_path, [_accept("a", mutation="survived", stability="flaky")])
    policy = GatePolicy(
        block_on_survived_mutation=False, require_stable_accepts=False
    )
    assert evaluate_gate(path, policy).passed is True


# ─── policy construction + malformed input ────────────────────────────────


def test_policy_from_mapping_filters_unknown_keys():
    policy = GatePolicy.from_mapping(
        {"enabled": True, "min_accept_rate": 0.7, "bogus": 123}
    )
    assert policy.enabled is True
    assert policy.min_accept_rate == 0.7


def test_policy_from_mapping_none_is_default():
    assert GatePolicy.from_mapping(None) == GatePolicy()


def test_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        evaluate_gate(tmp_path / "nope.json", GatePolicy())


def test_malformed_file_raises(tmp_path):
    p = tmp_path / "verdicts.json"
    p.write_text(json.dumps({"not_verdicts": []}))
    with pytest.raises(ValueError, match="verdicts"):
        evaluate_gate(p, GatePolicy())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
