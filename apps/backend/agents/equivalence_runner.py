"""Differential / behavioral-equivalence lane (RFC-0010 Phase 6).

For a language migration the new implementation must behave like the legacy one.
This lane runs the SAME golden corpus against both and asserts parity:

1. **Oracle capture** — run the legacy source over the declared input vectors in
   the hardened sandbox, serialising ``input → output`` (and error class) into a
   language-neutral ``findings/golden_corpus.json``. This is the only place
   untrusted code runs (the planner never executes anything — RFC-0010 §5.2).
2. **Differential run** — feed the same inputs to the new target impl and compare
   structurally (numeric tolerance, ordering, error-class normalisation).
3. **Parity verdict** — ``parity_ratio = matched / total``; pass iff
   ``>= parity_threshold`` AND every vector flagged ``critical`` matched.

This module's comparison + reporting core is pure and fully unit-tested; the
oracle/target *execution* is injected as a callable so the sandbox wiring stays
swappable and the logic stays testable.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Cross-language error taxonomy → a neutral class, so a Python ``ValueError`` and
# a Rust ``Err(InvalidInput)`` compare equal when both are "invalid input".
_ERROR_CLASS = {
    "valueerror": "invalid-input",
    "typeerror": "invalid-input",
    "keyerror": "not-found",
    "indexerror": "out-of-range",
    "zerodivisionerror": "arithmetic",
    "overflowerror": "arithmetic",
    "panic": "panic",
    "invalidinput": "invalid-input",
    "notfound": "not-found",
}

_FLOAT_TOL = 1e-9


def normalize_error(name: str | None) -> str | None:
    """Map a language-specific error/exception name to a neutral class."""
    if not name:
        return None
    return _ERROR_CLASS.get(name.strip().lower().replace("_", ""), name.strip().lower())


def values_match(expected: Any, actual: Any) -> bool:
    """Structural equality with numeric tolerance and order-insensitive lists.

    Lists compare element-wise (order matters for sequences); dicts by key; floats
    within ``_FLOAT_TOL``. ints/floats compare numerically across types.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        # bool is strict: True must not equal 1 (distinct behavioural outcomes).
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, float) or isinstance(actual, float):
            return math.isclose(
                expected, actual, rel_tol=_FLOAT_TOL, abs_tol=_FLOAT_TOL
            )
        return expected == actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            return False
        return all(values_match(expected[k], actual[k]) for k in expected)
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        return len(expected) == len(actual) and all(
            values_match(e, a) for e, a in zip(expected, actual)
        )
    return expected == actual


def vector_matches(expected: dict, actual: dict) -> bool:
    """Compare one corpus vector's outcome (value OR error class)."""
    exp_err = normalize_error(expected.get("error"))
    act_err = normalize_error(actual.get("error"))
    if exp_err or act_err:
        return exp_err == act_err
    return values_match(expected.get("output"), actual.get("output"))


@dataclass
class ParityReport:
    total: int
    matched: int
    mismatches: list[dict] = field(default_factory=list)
    critical_failed: list[dict] = field(default_factory=list)
    uncovered_modules: list[str] = field(default_factory=list)

    @property
    def parity_ratio(self) -> float:
        return (self.matched / self.total) if self.total else 0.0

    def passed(self, threshold: float) -> bool:
        return (
            self.total > 0
            and not self.critical_failed
            and self.parity_ratio >= threshold
        )

    def verdicts(self) -> list[dict]:
        """One ``equivalence``-lane verdict per vector (feeds val_block VAL-2)."""
        out = [
            {"lane": "equivalence", "verdict": "accept"} for _ in range(self.matched)
        ]
        out += [{"lane": "equivalence", "verdict": "reject"} for _ in self.mismatches]
        return out

    def claim(self, threshold: float) -> str:
        """Honest one-liner — never reads as full equivalence on partial parity."""
        pct = round(self.parity_ratio * 100, 1)
        msg = (
            f"Behavioral parity proven for {self.matched}/{self.total} golden vectors "
            f"({pct}%)"
        )
        if self.critical_failed:
            msg += f"; {len(self.critical_failed)} CRITICAL vector(s) diverged — NOT equivalent"
        elif not self.passed(threshold):
            msg += f"; below parity threshold {threshold} — treat as NOT equivalent"
        if self.uncovered_modules:
            msg += (
                f"; modules {', '.join(self.uncovered_modules)} UNPROVEN "
                "(no corpus coverage) — unverified"
            )
        return msg


def compare_corpus(
    golden: list[dict],
    candidate: list[dict],
    *,
    uncovered_modules: list[str] | None = None,
) -> ParityReport:
    """Compare candidate outputs against the golden oracle, vector by vector.

    Each entry is ``{"id", "output"?, "error"?, "critical"?}``. Candidate entries
    are matched to golden by ``id``; a missing candidate counts as a mismatch.
    """
    cand_by_id = {c.get("id"): c for c in candidate}
    matched = 0
    mismatches: list[dict] = []
    critical_failed: list[dict] = []
    for g in golden:
        gid = g.get("id")
        c = cand_by_id.get(gid)
        if c is not None and vector_matches(g, c):
            matched += 1
        else:
            entry = {
                "id": gid,
                "expected": g.get("output", g.get("error")),
                "actual": (c.get("output", c.get("error")) if c else "<no output>"),
            }
            mismatches.append(entry)
            if g.get("critical"):
                critical_failed.append(entry)
    return ParityReport(
        total=len(golden),
        matched=matched,
        mismatches=mismatches,
        critical_failed=critical_failed,
        uncovered_modules=list(uncovered_modules or []),
    )


def run_equivalence(
    manifest: dict,
    *,
    capture_oracle: Callable[[dict], list[dict]],
    run_candidate: Callable[[dict], list[dict]],
    parity_threshold: float = 1.0,
) -> ParityReport:
    """Orchestrate the differential lane (execution injected for testability).

    ``capture_oracle`` runs the legacy source over ``manifest`` in the sandbox and
    returns golden vectors; ``run_candidate`` runs the new impl and returns its
    vectors. Both are provided by the verify path (sandbox runners); this function
    owns only the comparison + honest reporting.
    """
    golden = capture_oracle(manifest)
    candidate = run_candidate(manifest)
    covered = {g.get("module") for g in golden if g.get("module")}
    declared = {
        f.get("module") for f in manifest.get("functions", []) if f.get("module")
    }
    uncovered = sorted(declared - covered)
    return compare_corpus(golden, candidate, uncovered_modules=uncovered)
