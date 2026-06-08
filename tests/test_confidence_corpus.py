"""Flaky-history wiring (#239) + scoring regression corpus (epic #232).

Two jobs:
  1. Pin the new flaky behaviour: confidence penalty, the deterministic
     accept→flag override, and the enrich_verdicts flaky map.
  2. A GOLDEN CORPUS — fixed verdict inputs → exact expected (verdict,
     confidence). Any unintended change to weights, normalisation, or the flaky
     penalty trips these, so scoring can't silently drift.
"""

from __future__ import annotations

import pytest
from agents.confidence import (
    FLAKY_PENALTY_FLOOR,
    apply_flaky_override,
    compute_confidence,
    enrich_verdicts,
)


def _verdict(verdict="accept", semantic="high", flaky=None, **summary):
    if flaky is not None:
        summary["flaky"] = flaky
    return {
        "test_id": "t",
        "verdict": verdict,
        "semantic_relevance": semantic,
        "signals_summary": summary,
    }


def _all_green(**extra):
    return dict(
        mutation="killed",
        stability="stable",
        semantic="high",
        coverage_new_lines=5,
        lint_promotion="no findings",
        **extra,
    )


# ─── flaky penalty on confidence ─────────────────────────────────────────


def test_no_flaky_is_unchanged():
    assert compute_confidence(_verdict(**_all_green())) == 1.0


def test_stable_flaky_classification_no_penalty():
    v = _verdict(flaky={"classification": "stable", "flip_rate": 0.0, "runs": 5}, **_all_green())
    assert compute_confidence(v) == 1.0


def test_new_flaky_classification_no_penalty():
    v = _verdict(flaky={"classification": "new", "flip_rate": 0.0, "runs": 1}, **_all_green())
    assert compute_confidence(v) == 1.0


def test_flaky_penalises_in_proportion_to_flip_rate():
    # base 1.0 * (1 - 0.25) = 0.75
    v = _verdict(flaky={"classification": "flaky", "flip_rate": 0.25, "runs": 8}, **_all_green())
    assert compute_confidence(v) == 0.75
    # base 1.0 * (1 - 0.5) = 0.5
    v2 = _verdict(flaky={"classification": "flaky", "flip_rate": 0.5, "runs": 8}, **_all_green())
    assert compute_confidence(v2) == 0.5


def test_flaky_penalty_floor():
    v = _verdict(flaky={"classification": "flaky", "flip_rate": 0.95, "runs": 8}, **_all_green())
    assert compute_confidence(v) == FLAKY_PENALTY_FLOOR


def test_flaky_missing_flip_rate_defaults_half():
    v = _verdict(flaky={"classification": "flaky", "runs": 8}, **_all_green())
    assert compute_confidence(v) == 0.5  # 1.0 * (1 - 0.5)


# ─── deterministic verdict override ──────────────────────────────────────


def test_override_demotes_accept_to_flag():
    v = _verdict(verdict="accept", flaky={"classification": "flaky", "flip_rate": 0.4, "runs": 6}, **_all_green())
    changed = apply_flaky_override(v)
    assert changed is True
    assert v["verdict"] == "flag"
    assert any("flaky-history" in r for r in v["reasons"])


def test_override_never_touches_reject():
    v = _verdict(verdict="reject", flaky={"classification": "flaky", "flip_rate": 0.4, "runs": 6})
    assert apply_flaky_override(v) is False
    assert v["verdict"] == "reject"


def test_override_noop_when_stable():
    v = _verdict(verdict="accept", flaky={"classification": "stable", "flip_rate": 0.0, "runs": 6})
    assert apply_flaky_override(v) is False
    assert v["verdict"] == "accept"


def test_override_noop_without_flaky():
    v = _verdict(verdict="accept", **_all_green())
    assert apply_flaky_override(v) is False


# ─── enrich_verdicts with flaky map ──────────────────────────────────────


def test_enrich_stamps_flaky_and_overrides():
    doc = {"verdicts": [_verdict(verdict="accept", **_all_green())]}
    flaky_map = {"t": {"classification": "flaky", "flip_rate": 0.5, "runs": 8}}
    enrich_verdicts(doc, flaky_map)
    v = doc["verdicts"][0]
    assert v["signals_summary"]["flaky"]["classification"] == "flaky"
    assert v["verdict"] == "flag"  # demoted
    assert v["signals_summary"]["confidence"] == 0.5  # penalised
    # demoted test no longer counts as accepted in the rollup
    assert doc["confidence_summary"]["accepted_count"] == 0


def test_enrich_without_map_is_c1_behaviour():
    doc = {"verdicts": [_verdict(verdict="accept", **_all_green())]}
    enrich_verdicts(doc)  # no flaky map
    v = doc["verdicts"][0]
    assert v["verdict"] == "accept"
    assert v["signals_summary"]["confidence"] == 1.0
    assert "flaky" not in v["signals_summary"]


# ─── GOLDEN CORPUS — fixed inputs → exact (verdict, confidence) ───────────
# Each entry: (id, input_verdict_dict, optional_flaky, expected_verdict,
# expected_confidence). Update deliberately when scoring intentionally changes.
_CORPUS = [
    (
        "clean-accept",
        _verdict(verdict="accept", **_all_green()),
        None,
        "accept",
        1.0,
    ),
    (
        "survived-mutant",
        _verdict(verdict="reject", mutation="survived", stability="stable",
                 semantic="high", coverage_new_lines=5, lint_promotion="no findings"),
        None,
        "reject",
        0.70,
    ),
    (
        "low-semantic-flag",
        _verdict(verdict="flag", mutation="killed", stability="stable",
                 semantic="low", coverage_new_lines=5, lint_promotion="no findings"),
        None,
        "flag",
        0.80,
    ),
    (
        "browser-no-coverage",
        _verdict(verdict="accept", mutation="killed", stability="stable",
                 semantic="high", lint_promotion="no findings"),
        None,
        "accept",
        1.0,
    ),
    (
        "zero-coverage",
        _verdict(verdict="flag", mutation="killed", stability="stable",
                 semantic="high", coverage_new_lines=0, lint_promotion="no findings"),
        None,
        "flag",
        0.85,
    ),
    (
        "flaky-demoted",
        _verdict(verdict="accept", **_all_green()),
        {"classification": "flaky", "flip_rate": 0.5, "runs": 8},
        "flag",
        0.5,
    ),
    (
        "all-bad-reject",
        _verdict(verdict="reject", mutation="survived", stability="flaky",
                 semantic="low", coverage_new_lines=0, lint_promotion="promoted to reject"),
        None,
        "reject",
        0.0,
    ),
]


@pytest.mark.parametrize(
    "case_id,verdict,flaky,exp_verdict,exp_conf",
    _CORPUS,
    ids=[c[0] for c in _CORPUS],
)
def test_scoring_corpus(case_id, verdict, flaky, exp_verdict, exp_conf):
    doc = {"verdicts": [verdict]}
    flaky_map = {"t": flaky} if flaky else None
    enrich_verdicts(doc, flaky_map)
    out = doc["verdicts"][0]
    assert out["verdict"] == exp_verdict, f"{case_id}: verdict drift"
    assert out["signals_summary"]["confidence"] == exp_conf, f"{case_id}: confidence drift"
