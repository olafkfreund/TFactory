"""Numeric confidence scoring for Evaluator verdicts (#238, epic #232).

The Evaluator emits *categorical* verdicts (accept / reject / flag) plus a
``signals_summary`` per test. That's authoritative but un-thresholdable: a
consumer can't say "only commit tests above 0.8" or rank by trust. This module
derives a deterministic ``confidence`` in [0.0, 1.0] from the same five signals
the verdict was based on, so the number *explains* the category rather than
competing with it.

Design notes:
  - Pure / side-effect-free — mirrors the other Evaluator primitives
    (coverage_delta.py, stability_runner.py …) so it's unit-testable without
    Docker or an LLM.
  - Each signal maps to a sub-score in [0,1]; confidence is their weighted mean,
    **renormalised over the signals actually present**. A browser-lane test with
    coverage "N/A" is therefore not penalised for missing coverage — its weight
    is dropped and the rest are rescaled.
  - The categorical verdict is NOT used as an input (no circular "accept → high
    score"). Rejects score low naturally because a reject is driven by a 0.0
    signal (survived mutant, flaky run, low semantic relevance).

Confidence does not replace the verdict; the Triager still ranks by category
first. It's additive metadata for thresholding, badges (#241) and the Backstage
scorecard (#240).
"""

from __future__ import annotations

from typing import Any

# ─── Weights ────────────────────────────────────────────────────────────
# Sum to 1.0. Mutation is weighted highest because a SURVIVED mutant is the
# strongest evidence an assertion is tautological; semantic/coverage are
# softer. Tunable here in one place.
WEIGHTS = {
    "mutation": 0.30,
    "stability": 0.25,
    "semantic": 0.20,
    "coverage": 0.15,
    "lint": 0.10,
}

# Commit-readiness thresholds over the accepted-tests mean confidence.
READINESS_HIGH = 0.80
READINESS_MEDIUM = 0.60


def _norm(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def _mutation_subscore(value: object) -> float | None:
    """killed → kept the mutant out (good); survived → tautological (bad)."""
    v = _norm(value)
    if v in ("killed",):
        return 1.0
    if v in ("survived",):
        return 0.0
    if v in ("no_mutation", "no mutation", "no_mutant"):
        return 0.5  # nothing to mutate — neutral, not evidence either way
    if v in ("error", "syntax_error", "timeout"):
        return 0.5
    return None  # missing / unknown → drop from the average


def _stability_subscore(value: object) -> float | None:
    v = _norm(value)
    if v == "stable":
        return 1.0
    if v in ("flaky", "consistent_fail"):
        return 0.0
    if v == "error":
        return 0.4
    return None


def _semantic_subscore(value: object) -> float | None:
    v = _norm(value)
    if v == "high":
        return 1.0
    if v == "medium":
        return 0.5
    if v == "low":
        return 0.0
    return None


def _coverage_subscore(summary: dict) -> float | None:
    """Exercises new lines → 1.0; exercises none → 0.0; N/A (browser) → drop.

    Prefers the explicit ``coverage_new_lines`` count; falls back to the
    ``coverage_delta_pct`` sign when only the percentage is present.
    """
    new_lines = summary.get("coverage_new_lines")
    if isinstance(new_lines, (int, float)) and not isinstance(new_lines, bool):
        return 1.0 if new_lines > 0 else 0.0
    pct = summary.get("coverage_delta_pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool):
        return 1.0 if pct > 0 else 0.0
    return None  # "N/A (browser lane)" / missing → drop


def _lint_subscore(value: object) -> float | None:
    """Clean lint → 1.0; promoted/high findings → 0.0; otherwise neutral."""
    if value is None:
        return None
    v = _norm(value)
    if v == "":
        return None
    if any(tok in v for tok in ("no finding", "clean", "none", "no issues")):
        return 1.0
    if any(tok in v for tok in ("reject", "promoted", "high")):
        return 0.0
    return 0.7  # present but benign mediums — slight discount


def compute_confidence(verdict: dict) -> float:
    """Return a deterministic confidence in [0.0, 1.0] for one verdict dict.

    Reads ``signals_summary`` (coverage_new_lines / coverage_delta_pct,
    stability, mutation, lint_promotion) and top-level ``semantic_relevance``.
    Weighted mean over present signals, rounded to 2 dp.
    """
    summary = verdict.get("signals_summary") or {}
    subs = {
        "mutation": _mutation_subscore(summary.get("mutation")),
        "stability": _stability_subscore(summary.get("stability")),
        "semantic": _semantic_subscore(verdict.get("semantic_relevance")),
        "coverage": _coverage_subscore(summary),
        "lint": _lint_subscore(summary.get("lint_promotion")),
    }
    num = 0.0
    den = 0.0
    for key, sub in subs.items():
        if sub is None:
            continue
        w = WEIGHTS[key]
        num += w * sub
        den += w
    if den == 0.0:
        # No usable signals — fall back to a verdict-shaped neutral so the
        # field is always populated.
        base = {"accept": 0.6, "flag": 0.4, "reject": 0.1}.get(
            _norm(verdict.get("verdict")), 0.5
        )
    else:
        base = num / den
    return round(base * _flaky_penalty(summary), 2)


# ─── Flaky-history wiring (#239) ─────────────────────────────────────────
# Cross-run flip-rate is a distinct kind of evidence from the single-run
# signals: a test can pass all 5 this run yet have flipped pass/fail across
# history. Rather than fold it into the weighted mean (which would re-base
# every C1 number), it applies as a *multiplicative penalty* — no history or a
# STABLE classification leaves confidence unchanged; a FLAKY test is discounted
# in proportion to its flip-rate.

# Floor so even a coin-flip test keeps a small, non-zero confidence.
FLAKY_PENALTY_FLOOR = 0.3


def _flaky_penalty(summary: dict) -> float:
    """Return a multiplier in [FLAKY_PENALTY_FLOOR, 1.0] from ``summary.flaky``.

    Only a ``classification == "flaky"`` entry penalises; NEW/STABLE/missing
    return 1.0 (no effect — keeps C1 behaviour intact).
    """
    flaky = summary.get("flaky")
    if not isinstance(flaky, dict):
        return 1.0
    if _norm(flaky.get("classification")) != "flaky":
        return 1.0
    flip = flaky.get("flip_rate")
    flip = (
        float(flip)
        if isinstance(flip, (int, float)) and not isinstance(flip, bool)
        else 0.5
    )
    return max(FLAKY_PENALTY_FLOOR, 1.0 - flip)


def apply_flaky_override(verdict: dict) -> bool:
    """Deterministically demote an ``accept`` of a FLAKY test to ``flag``.

    Cross-run flakiness is authoritative regardless of the LLM's call: a test
    that flips across runs must never silently land. Never upgrades a verdict.
    Returns True if the verdict label was changed.
    """
    summary = verdict.get("signals_summary")
    if not isinstance(summary, dict):
        return False
    flaky = summary.get("flaky")
    if not isinstance(flaky, dict):
        return False
    if _norm(flaky.get("classification")) != "flaky":
        return False
    if _norm(verdict.get("verdict")) != "accept":
        return False
    verdict["verdict"] = "flag"
    reasons = verdict.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
        verdict["reasons"] = reasons
    reasons.append(
        f"flaky-history: flip_rate={flaky.get('flip_rate')} over "
        f"{flaky.get('runs')} runs — demoted accept→flag (#239)"
    )
    return True


# ─── consistent_fail reason accuracy (#629) ──────────────────────────────
# The Evaluator's judge LLM produces `reasons` prose per test with no
# deterministic signal telling an import/collection error apart from a
# genuine assertion failure — so for a CONSISTENT_FAIL it sometimes guesses
# "the subject module is not resolvable/importable", even when the test
# actually ran and failed a real assertion. That sends a human chasing a
# phantom import/co-mount bug. ``stability_runner.StabilityResult.failure_kind``
# (#629) classifies the captured pytest output deterministically; this
# applies that classification to the verdict's `reasons`, same pattern as
# ``apply_flaky_override`` above. Never changes the verdict category — a
# consistent_fail stays a reject/flag — only the reason text.


def apply_consistent_fail_reason(
    verdict: dict[str, Any], failure_info: dict[str, Any] | None
) -> bool:
    """Replace ``reasons`` with an accurate, classifier-derived string for a
    ``consistent_fail`` verdict (#629).

    Args:
        verdict: One verdict dict from ``verdicts.json``.
        failure_info: ``{"failure_kind": "import" | "assertion" | "unknown",
        "rerun_count": int}`` derived from the test's ``StabilityResult``, or
        ``None`` when stability wasn't computed for this test.

    Returns True if ``reasons`` was replaced. No-ops (returns False) when the
    verdict's stability signal isn't ``consistent_fail``, when there's no
    failure_info, or when the classifier couldn't tell (``"unknown"``) — in
    that last case the LLM's own reason is left alone rather than replaced
    with a non-answer.
    """
    summary = verdict.get("signals_summary")
    if (
        not isinstance(summary, dict)
        or _norm(summary.get("stability")) != "consistent_fail"
    ):
        return False
    if not isinstance(failure_info, dict):
        return False
    kind = failure_info.get("failure_kind")
    runs = failure_info.get("rerun_count", 3)
    if kind == "assertion":
        reason = (
            f"consistent test failure across {runs} runs — the test executed "
            "and its assertions failed (subject behaviour is wrong), not an "
            "import/collection error"
        )
    elif kind == "import":
        reason = (
            f"consistent test failure across {runs} runs — the subject module "
            "could not be imported/collected in the sandbox (import/collection "
            "error)"
        )
    else:
        return False
    verdict["reasons"] = [reason]
    return True


def aggregate_confidence(verdicts: list[dict]) -> dict:
    """Run-level rollup over per-verdict confidences.

    Returns ``{count, mean, accepted_count, accepted_mean, commit_readiness}``.
    ``commit_readiness`` is bucketed off the accepted-tests mean (the tests that
    would actually land), falling back to the overall mean when nothing is
    accepted.
    """
    confs = [
        v["signals_summary"]["confidence"]
        for v in verdicts
        if isinstance(v.get("signals_summary"), dict)
        and isinstance(v["signals_summary"].get("confidence"), (int, float))
    ]
    accepted = [
        v["signals_summary"]["confidence"]
        for v in verdicts
        if _norm(v.get("verdict")) == "accept"
        and isinstance(v.get("signals_summary"), dict)
        and isinstance(v["signals_summary"].get("confidence"), (int, float))
    ]
    mean = round(sum(confs) / len(confs), 2) if confs else 0.0
    accepted_mean = round(sum(accepted) / len(accepted), 2) if accepted else 0.0
    basis = accepted_mean if accepted else mean
    if basis >= READINESS_HIGH:
        readiness = "high"
    elif basis >= READINESS_MEDIUM:
        readiness = "medium"
    else:
        readiness = "low"
    return {
        "count": len(confs),
        "mean": mean,
        "accepted_count": len(accepted),
        "accepted_mean": accepted_mean,
        "commit_readiness": readiness,
    }


def enrich_verdicts(
    doc: dict[str, Any],
    flaky_by_test_id: dict[str, Any] | None = None,
    failure_kind_by_test_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mutate ``doc`` in place: stamp flaky history, confidence + run rollup.

    For each verdict, in order:
      1. stamp ``signals_summary.flaky`` from ``flaky_by_test_id`` (#239),
      2. apply the deterministic flaky override (accept→flag for FLAKY tests),
      3. fix the ``reasons`` narrative for a consistent_fail using the
         classifier-derived ``failure_kind_by_test_id`` (#629),
      4. compute ``signals_summary.confidence`` (now flaky-penalised).
    Then add the top-level ``confidence_summary``. Safe on an empty doc and when
    ``flaky_by_test_id`` / ``failure_kind_by_test_id`` are None (then behaves
    exactly like C1). Returns ``doc``.
    """
    flaky_by_test_id = flaky_by_test_id or {}
    failure_kind_by_test_id = failure_kind_by_test_id or {}
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        return doc
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        summary = v.get("signals_summary")
        if not isinstance(summary, dict):
            summary = {}
            v["signals_summary"] = summary
        test_id = v.get("test_id")
        test_id = test_id if isinstance(test_id, str) else None
        flaky = flaky_by_test_id.get(test_id) if test_id is not None else None
        if isinstance(flaky, dict):
            summary["flaky"] = flaky
        apply_flaky_override(v)
        failure_info = (
            failure_kind_by_test_id.get(test_id) if test_id is not None else None
        )
        apply_consistent_fail_reason(v, failure_info)
        summary["confidence"] = compute_confidence(v)
    doc["confidence_summary"] = aggregate_confidence(verdicts)
    return doc
