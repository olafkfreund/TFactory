"""Tests for the numeric confidence scorer (#238, epic #232).

Pure module — no Docker/LLM. Pins the weight math, signal normalisation,
renormalisation over missing signals, the run-level rollup, and the evaluator
enrichment shape.
"""

from __future__ import annotations

import pytest
from agents.confidence import (
    READINESS_HIGH,
    WEIGHTS,
    aggregate_confidence,
    compute_confidence,
    enrich_verdicts,
)


def _verdict(verdict="accept", semantic="high", **summary):
    return {
        "test_id": "t",
        "verdict": verdict,
        "semantic_relevance": semantic,
        "signals_summary": summary,
    }


# ─── weight invariants ──────────────────────────────────────────────────


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


# ─── all-green vs all-bad ────────────────────────────────────────────────


def test_all_green_is_max_confidence():
    v = _verdict(
        verdict="accept",
        semantic="high",
        mutation="killed",
        stability="stable",
        coverage_new_lines=7,
        lint_promotion="no findings",
    )
    assert compute_confidence(v) == 1.0


def test_all_bad_is_min_confidence():
    v = _verdict(
        verdict="reject",
        semantic="low",
        mutation="survived",
        stability="flaky",
        coverage_new_lines=0,
        lint_promotion="2 promoted to reject",
    )
    assert compute_confidence(v) == 0.0


# ─── individual signal pulls ─────────────────────────────────────────────


def test_survived_mutant_dominates_downward():
    # Everything green except mutation survived (the heaviest signal).
    v = _verdict(
        mutation="survived",
        stability="stable",
        semantic="high",
        coverage_new_lines=5,
        lint_promotion="no findings",
    )
    c = compute_confidence(v)
    # 1.0 across all but mutation(0.0, weight .30) → 0.70
    assert c == 0.70


def test_low_semantic_pulls_down():
    v = _verdict(
        mutation="killed",
        stability="stable",
        semantic="low",
        coverage_new_lines=5,
        lint_promotion="no findings",
    )
    # semantic 0.0 at weight .20 → 0.80
    assert compute_confidence(v) == 0.80


# ─── renormalisation over missing signals (browser lane) ─────────────────


def test_browser_lane_coverage_na_not_penalised():
    # No coverage fields at all (browser lane) → coverage weight dropped,
    # remaining green signals still yield 1.0.
    v = _verdict(
        mutation="killed",
        stability="stable",
        semantic="high",
        lint_promotion="no findings",
    )
    assert compute_confidence(v) == 1.0


def test_only_one_signal_present():
    v = _verdict(verdict="flag", semantic=None, mutation="killed")
    # mutation killed is the only usable signal → 1.0
    assert compute_confidence(v) == 1.0


def test_no_signals_falls_back_to_verdict_shape():
    assert compute_confidence(_verdict(verdict="accept", semantic=None)) == 0.6
    assert compute_confidence(_verdict(verdict="flag", semantic=None)) == 0.4
    assert compute_confidence(_verdict(verdict="reject", semantic=None)) == 0.1


# ─── coverage fallback to delta_pct ──────────────────────────────────────


def test_coverage_falls_back_to_delta_pct():
    v = _verdict(
        mutation="killed",
        stability="stable",
        semantic="high",
        coverage_delta_pct=3.2,  # positive → covered
        lint_promotion="no findings",
    )
    assert compute_confidence(v) == 1.0

    v_zero = _verdict(
        mutation="killed",
        stability="stable",
        semantic="high",
        coverage_delta_pct=0.0,  # no new coverage
        lint_promotion="no findings",
    )
    # coverage 0.0 at weight .15 → 0.85
    assert compute_confidence(v_zero) == 0.85


# ─── case-insensitivity ──────────────────────────────────────────────────


def test_signal_values_case_insensitive():
    v = _verdict(
        mutation="KILLED",
        stability="Stable",
        semantic="HIGH",
        coverage_new_lines=1,
        lint_promotion="No Findings",
    )
    assert compute_confidence(v) == 1.0


# ─── run-level aggregate ─────────────────────────────────────────────────


def test_aggregate_basic():
    verdicts = [
        _verdict(verdict="accept", **{"confidence": 0.9}),
        _verdict(verdict="accept", **{"confidence": 0.7}),
        _verdict(verdict="reject", **{"confidence": 0.1}),
    ]
    agg = aggregate_confidence(verdicts)
    assert agg["count"] == 3
    assert agg["accepted_count"] == 2
    assert agg["accepted_mean"] == 0.8
    assert agg["commit_readiness"] == "high"
    assert agg["mean"] == round((0.9 + 0.7 + 0.1) / 3, 2)


def test_aggregate_readiness_buckets():
    hi = aggregate_confidence([_verdict("accept", confidence=READINESS_HIGH)])
    assert hi["commit_readiness"] == "high"
    med = aggregate_confidence([_verdict("accept", confidence=0.65)])
    assert med["commit_readiness"] == "medium"
    lo = aggregate_confidence([_verdict("accept", confidence=0.3)])
    assert lo["commit_readiness"] == "low"


def test_aggregate_empty():
    agg = aggregate_confidence([])
    assert agg == {
        "count": 0,
        "mean": 0.0,
        "accepted_count": 0,
        "accepted_mean": 0.0,
        "commit_readiness": "low",
    }


def test_aggregate_no_accepted_uses_overall_mean():
    verdicts = [_verdict(verdict="flag", confidence=0.5)]
    agg = aggregate_confidence(verdicts)
    assert agg["accepted_count"] == 0
    assert agg["mean"] == 0.5
    assert agg["commit_readiness"] == "low"  # 0.5 < 0.60


# ─── enrich_verdicts (evaluator hook shape) ──────────────────────────────


def test_enrich_stamps_confidence_and_summary():
    doc = {
        "verdicts": [
            _verdict(
                verdict="accept",
                semantic="high",
                mutation="killed",
                stability="stable",
                coverage_new_lines=4,
                lint_promotion="no findings",
            ),
        ]
    }
    out = enrich_verdicts(doc)
    assert out is doc
    assert out["verdicts"][0]["signals_summary"]["confidence"] == 1.0
    assert out["confidence_summary"]["commit_readiness"] == "high"
    assert out["confidence_summary"]["accepted_count"] == 1


def test_enrich_handles_missing_signals_summary():
    doc = {"verdicts": [{"test_id": "x", "verdict": "flag", "semantic_relevance": "medium"}]}
    enrich_verdicts(doc)
    assert "confidence" in doc["verdicts"][0]["signals_summary"]


def test_enrich_empty_doc():
    doc = {"verdicts": []}
    enrich_verdicts(doc)
    assert doc["confidence_summary"]["count"] == 0


def test_enrich_non_list_verdicts_is_noop():
    doc = {"verdicts": None}
    out = enrich_verdicts(doc)
    assert out == {"verdicts": None}


@pytest.mark.parametrize(
    "mutation,expected_present",
    [("no_mutation", True), ("error", True), ("", False), (None, False)],
)
def test_mutation_neutral_and_missing(mutation, expected_present):
    v = _verdict(mutation=mutation, stability="stable", semantic="high")
    # Just assert it computes a float in range; presence affects renormalisation.
    c = compute_confidence(v)
    assert 0.0 <= c <= 1.0
