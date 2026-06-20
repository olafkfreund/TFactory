#!/usr/bin/env python3
"""RFC-0006 never-overclaim gate (reference implementation).

The integrity rule: a completion may report a verification status ONLY at the
level it actually proved, and must declare every gap. This function normalizes a
producer's `verification` block so it can NEVER claim more than was proven:

  - A missing/empty block  -> VAL-0, "NOT VERIFIED" (never silently "tested").
  - achieved_level is RECOMPUTED from the level outcomes, not trusted:
      * the highest level with status "passed",
      * but capped below the LOWEST failed level (a broken floor caps the ceiling).
    If the producer declared a higher achieved_level, that is an overclaim and is
    downgraded (recorded in `_gate.violations`).
  - Every failed/not_run/skipped level must carry a `reason`; a missing one is
    injected and flagged.
  - `claim` is overwritten with an honest, computed one-liner.

Pure + dependency-free so PFactory / AIFactory / TFactory can vendor it.
Run directly to execute the self-tests: `python3 scripts/verification_gate.py`.
"""

from __future__ import annotations

from typing import TypedDict

LEVELS = ["VAL-0", "VAL-1", "VAL-2", "VAL-3", "VAL-4"]
_NON_PASSED = {"failed", "not_run", "skipped"}


class LevelEntry(TypedDict, total=False):
    """One rung of the assurance ladder in a verification block.

    `level` and `status` are expected on every entry; the remainder
    (`reason`/`risk`/`evidence` and any producer extras) are optional, so the
    whole TypedDict is `total=False`.
    """

    level: str
    status: str
    reason: str
    risk: str
    evidence: str


class GateResult(TypedDict):
    """The `_gate` audit stamp recording how the block was normalized."""

    violations: list[str]
    downgraded: bool


class VerificationBlock(TypedDict, total=False):
    """A producer's verification block (input) / the normalized copy (output).

    Loose by design (`total=False`): producers may omit fields, and the gate
    fills/overwrites `achieved_level`, `claim`, `levels`, and `_gate` so the
    result can never overclaim.
    """

    target_level: str
    achieved_level: str
    levels: list[LevelEntry]
    claim: str
    _gate: GateResult


def _idx(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def normalize_verification(block: VerificationBlock | None) -> VerificationBlock:
    """Return an honest, never-overclaiming copy of a verification block."""
    violations: list[str] = []

    if not isinstance(block, dict) or not block.get("levels"):
        return {
            "target_level": (block or {}).get("target_level", "VAL-0"),
            "achieved_level": "VAL-0",
            "levels": [
                {
                    "level": "VAL-0",
                    "status": "not_run",
                    "reason": "no verification block declared by the producer",
                    "risk": "nothing was proven; treat as unverified",
                }
            ],
            "claim": "NOT VERIFIED — no verification was declared; treat as unproven.",
            "_gate": {"violations": ["missing_verification_block"], "downgraded": True},
        }

    levels: list[LevelEntry] = [lvl.copy() for lvl in block["levels"]]

    # Force a reason on every gap (honesty: a gap with no explanation is itself a gap).
    for lvl in levels:
        if lvl.get("status") in _NON_PASSED and not lvl.get("reason"):
            lvl["reason"] = "(no reason provided)"
            violations.append(f"missing_reason:{lvl.get('level')}")

    passed = [lvl["level"] for lvl in levels if lvl.get("status") == "passed"]
    failed = [lvl["level"] for lvl in levels if lvl.get("status") == "failed"]

    # A failure at any level caps the ceiling below it (a broken floor is not "passed above").
    cap = min((_idx(f) for f in failed), default=len(LEVELS))
    eligible = [p for p in passed if _idx(p) < cap]
    true_achieved = max(eligible, key=_idx) if eligible else "VAL-0"

    declared = block.get("achieved_level", "VAL-0")
    if _idx(declared) > _idx(true_achieved):
        violations.append(f"overclaim:{declared}>actual:{true_achieved}")
    achieved = true_achieved  # always the truth, never the (possibly inflated) claim

    # Honest computed claim: what passed, and every level above it that did not.
    gaps = [
        f"{lvl['level']} {lvl.get('status')} ({lvl.get('reason')})"
        for lvl in levels
        if _idx(lvl["level"]) > _idx(achieved) and lvl.get("status") in _NON_PASSED
    ]
    if achieved == "VAL-0" and not passed:
        claim = "NOT VERIFIED beyond static at best — " + (
            "; ".join(gaps) if gaps else "no level passed"
        )
    else:
        claim = f"Verified to {achieved}."
        if gaps:
            claim += " NOT verified: " + "; ".join(gaps) + "."

    out: VerificationBlock = block.copy()
    out["levels"] = levels
    out["achieved_level"] = achieved
    out["claim"] = claim
    out["_gate"] = {"violations": violations, "downgraded": bool(violations)}
    return out


# --------------------------------------------------------------------------- #
# Self-tests (run: python3 scripts/verification_gate.py)
# --------------------------------------------------------------------------- #
def _test() -> None:
    # 1. Overclaim is downgraded to the truth.
    r = normalize_verification(
        {
            "target_level": "VAL-3",
            "achieved_level": "VAL-3",
            "levels": [
                {"level": "VAL-0", "status": "passed"},
                {"level": "VAL-2", "status": "passed"},
                {"level": "VAL-3", "status": "not_run", "reason": "no sandbox target"},
            ],
        }
    )
    assert r["achieved_level"] == "VAL-2", r
    assert any(v.startswith("overclaim") for v in r["_gate"]["violations"]), r
    assert "NOT verified: VAL-3 not_run" in r["claim"], r["claim"]

    # 2. Missing block -> VAL-0, never "tested".
    r = normalize_verification(None)
    assert r["achieved_level"] == "VAL-0" and "NOT VERIFIED" in r["claim"], r

    # 3. A failure caps the ceiling (cannot "pass" above a broken floor).
    r = normalize_verification(
        {
            "target_level": "VAL-2",
            "achieved_level": "VAL-2",
            "levels": [
                {"level": "VAL-0", "status": "failed", "reason": "lint errors"},
                {"level": "VAL-2", "status": "passed"},
            ],
        }
    )
    assert r["achieved_level"] == "VAL-0", r

    # 4. Missing reason on a gap is injected + flagged.
    r = normalize_verification(
        {
            "target_level": "VAL-2",
            "achieved_level": "VAL-0",
            "levels": [{"level": "VAL-2", "status": "not_run"}],
        }
    )
    assert any(v.startswith("missing_reason") for v in r["_gate"]["violations"]), r
    assert r["levels"][0]["reason"] == "(no reason provided)", r

    # 5. Honest happy path: no overclaim, clean claim.
    r = normalize_verification(
        {
            "target_level": "VAL-2",
            "achieved_level": "VAL-2",
            "levels": [
                {"level": "VAL-0", "status": "passed"},
                {"level": "VAL-2", "status": "passed", "evidence": "idempotence: 0 changed"},
            ],
        }
    )
    assert r["achieved_level"] == "VAL-2" and not r["_gate"]["downgraded"], r
    assert r["claim"] == "Verified to VAL-2.", r["claim"]

    print("verification_gate self-tests: 5 passed")


if __name__ == "__main__":
    _test()
