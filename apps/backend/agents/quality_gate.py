"""Quality gate — WS1 of the enterprise 90-day plan (PR-native gate).

Turns the Evaluator's ``findings/verdicts.json`` into a single **pass/fail**
decision against a configurable policy, so the Triager can publish a GitHub
status check (``tools/pr_status.py``) that gates merge — making "Test" a gate
in the workflow people already use, not a separate destination.

Pure compute, no I/O beyond reading the verdicts file. The policy is sourced
from the ``quality_gate:`` block of ``.tfactory.yml`` (see
``tfactory_yml/schema.py::QualityGatePolicy``); ``GatePolicy.from_mapping``
builds the in-engine policy from that parsed block.

Verdict shape (written by the Evaluator)::

    {"verdicts": [
       {"test_id": "...", "verdict": "accept"|"flag"|"reject",
        "signals_summary": {
           "coverage_delta_pct": 5.2 | null,
           "stability": "stable"|"flaky"|"consistent_fail"|"error",
           "mutation": "killed"|"survived"|"no_mutation"|"error",
           "ci_parity": "yes"|"mocked-subject"|"no"|"not computed"}}]}

The gate is **opt-in** (``enabled=False`` by default) — consistent with the
no-surprises posture of the Triager's other side-effects.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# GitHub commit-status states we emit (subset of error/failure/pending/success).
STATE_SUCCESS = "success"
STATE_FAILURE = "failure"

_VALID_VERDICTS = frozenset({"accept", "flag", "reject"})


@dataclass(frozen=True)
class GatePolicy:
    """Thresholds that decide whether a test suite passes the PR gate.

    Defaults are deliberately permissive but meaningful: require at least one
    trustworthy (accepted) test, and fail on accepted tests that carry a
    self-contradictory signal (a survived mutant, a mocked-out subject, or
    instability) — guardrails that should never trip if the Evaluator did its
    job, but which keep a bad verdict from silently passing the gate.
    """

    enabled: bool = False
    min_accepted: int = 1
    min_accept_rate: float = 0.0  # accepted / evaluated, 0..1
    max_flag_rate: float = 1.0  # flagged / evaluated, 0..1
    block_on_reject: bool = False  # rejects are dropped junk, not SUT bugs
    block_on_survived_mutation: bool = True
    block_on_mocked_subject: bool = True
    require_stable_accepts: bool = True
    context: str = "TFactory / tests"  # GitHub status-check label

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> GatePolicy:
        """Build a policy from the parsed ``quality_gate:`` block (or None)."""
        if not data:
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class GateResult:
    """Outcome of evaluating the gate against a verdicts file."""

    passed: bool
    state: str  # STATE_SUCCESS | STATE_FAILURE
    summary: str  # one-line, ≤140 chars (status desc)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    counts: dict[str, int] = field(default_factory=dict)


def _load_verdicts(verdicts_path: Path) -> list[dict[str, Any]]:
    """Read the ``verdicts`` array; raise ValueError on a malformed file."""
    if not verdicts_path.exists():
        raise ValueError(f"verdicts.json not found: {verdicts_path}")
    doc = json.loads(verdicts_path.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("verdicts"), list):
        raise ValueError("verdicts.json missing 'verdicts' array")
    return [v for v in doc["verdicts"] if isinstance(v, dict)]


def evaluate_gate(verdicts_path: Path, policy: GatePolicy) -> GateResult:
    """Compute the pass/fail gate result for a verdicts file.

    Counts accept/flag/reject, derives accept/flag rates, and applies the
    policy. Per-accepted-test signal guardrails (survived mutation / mocked
    subject / instability) are checked so a contradictory accept can't pass.
    """
    verdicts = _load_verdicts(verdicts_path)
    counts = {"accept": 0, "flag": 0, "reject": 0, "total": 0}
    for v in verdicts:
        label = v.get("verdict")
        if label in _VALID_VERDICTS:
            counts[label] += 1
            counts["total"] += 1

    total = counts["total"]
    accepted = counts["accept"]
    flagged = counts["flag"]
    rejected = counts["reject"]
    accept_rate = accepted / total if total else 0.0
    flag_rate = flagged / total if total else 0.0

    reasons: list[str] = []

    if total == 0:
        if policy.min_accepted > 0:
            reasons.append("no tests were evaluated")
    else:
        if accepted < policy.min_accepted:
            reasons.append(
                f"accepted {accepted} < required minimum {policy.min_accepted}"
            )
        if accept_rate < policy.min_accept_rate:
            reasons.append(
                f"accept-rate {accept_rate:.0%} < required {policy.min_accept_rate:.0%}"
            )
        if flag_rate > policy.max_flag_rate:
            reasons.append(
                f"flag-rate {flag_rate:.0%} > allowed {policy.max_flag_rate:.0%}"
            )
        if policy.block_on_reject and rejected > 0:
            reasons.append(f"{rejected} rejected test(s) and block_on_reject is set")

    # Per-accepted-test signal guardrails.
    for v in verdicts:
        if v.get("verdict") != "accept":
            continue
        tid = v.get("test_id", "?")
        sig = v.get("signals_summary")
        if not isinstance(sig, dict):
            continue
        if policy.block_on_survived_mutation and sig.get("mutation") == "survived":
            reasons.append(f"accepted test {tid!r} survived its mutation probe")
        if policy.block_on_mocked_subject and sig.get("ci_parity") == "mocked-subject":
            reasons.append(f"accepted test {tid!r} mocks its subject (ci_parity)")
        if policy.require_stable_accepts:
            stability = sig.get("stability")
            if stability is not None and stability != "stable":
                reasons.append(f"accepted test {tid!r} is not stable ({stability})")

    passed = not reasons
    state = STATE_SUCCESS if passed else STATE_FAILURE
    summary = _summarize(accepted, flagged, rejected, accept_rate, passed)
    return GateResult(
        passed=passed,
        state=state,
        summary=summary,
        reasons=tuple(reasons),
        counts=counts,
    )


def _summarize(
    accepted: int, flagged: int, rejected: int, accept_rate: float, passed: bool
) -> str:
    """One-line status description, kept within GitHub's 140-char limit."""
    verdict = "passed" if passed else "failed"
    s = (
        f"Gate {verdict}: {accepted} accepted, {flagged} flagged, "
        f"{rejected} rejected (accept-rate {accept_rate:.0%})"
    )
    return s[:140]
