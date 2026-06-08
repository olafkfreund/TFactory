"""Tests for honoring the RFC-0002 execution scope (#247, epic #244)."""

from __future__ import annotations

from agents.contract_scope import (
    apply_execution_scope,
    build_execution_scope,
    coverage_target_met,
)
from agents.task_contract import TfactoryProfile


def _profile(**kw):
    return TfactoryProfile(**kw)


# ─── build_execution_scope ───────────────────────────────────────────────


def test_none_profile():
    assert build_execution_scope(None) is None


def test_empty_scopes_returns_none():
    assert build_execution_scope(_profile(lanes=("unit",))) is None


def test_full_scope():
    s = build_execution_scope(
        _profile(coverage_target=0.8, mutation_scope=("src/**.py",), security_scope=("owasp:*",))
    )
    assert s["coverage_target"] == 0.8
    assert s["mutation_scope"] == ["src/**.py"]
    assert s["security_scope"] == ["owasp:*"]
    assert s["security_delegated"] is True
    assert s["source"] == "rfc-0002-contract"


def test_security_not_delegated_when_empty():
    s = build_execution_scope(_profile(coverage_target=0.5))
    assert s["security_delegated"] is False


# ─── coverage_target_met ─────────────────────────────────────────────────


def test_coverage_met_none_without_target():
    assert coverage_target_met({"verdicts": []}, None) is None


def test_coverage_met_none_without_data():
    assert coverage_target_met({"verdicts": [{"signals_summary": {}}]}, 0.8) is None


def test_coverage_met_true_on_positive_delta():
    doc = {"verdicts": [{"signals_summary": {"coverage_delta_pct": 3.0}}]}
    assert coverage_target_met(doc, 0.8) is True


def test_coverage_met_false_on_zero_delta():
    doc = {"verdicts": [{"signals_summary": {"coverage_delta_pct": 0.0}}]}
    assert coverage_target_met(doc, 0.8) is False


# ─── apply_execution_scope ───────────────────────────────────────────────


def test_apply_stamps_scope_and_coverage_met():
    doc = {"verdicts": [{"signals_summary": {"coverage_delta_pct": 2.0}}]}
    apply_execution_scope(doc, _profile(coverage_target=0.8, mutation_scope=("a.py",)))
    assert doc["execution_scope"]["coverage_target"] == 0.8
    assert doc["execution_scope"]["coverage_target_met"] is True


def test_apply_noop_without_scope():
    doc = {"verdicts": []}
    apply_execution_scope(doc, _profile(lanes=("unit",)))
    assert "execution_scope" not in doc


def test_apply_noop_without_profile():
    doc = {"verdicts": []}
    apply_execution_scope(doc, None)
    assert "execution_scope" not in doc
