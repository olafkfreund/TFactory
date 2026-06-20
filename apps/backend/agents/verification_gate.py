#!/usr/bin/env python3
"""RFC-0006 never-overclaim gate — vendored from Factory hub (RFC-0006 #74).

Vendored verbatim from `Factory/scripts/verification_gate.py` (the reference
implementation, kept the single source of truth). Pure + dependency-free; sync
from the hub (only lint-conformance renames here, e.g. l→lvl for E741). Used by
`agents.val_block` to recompute an honest
`achieved_level` from the real lane outcomes of a verify run.

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

LEVELS = ["VAL-0", "VAL-1", "VAL-2", "VAL-3", "VAL-4"]
_NON_PASSED = {"failed", "not_run", "skipped"}


def _idx(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def normalize_verification(block: dict | None) -> dict:
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

    levels = [dict(lvl) for lvl in block["levels"]]

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

    out = dict(block)
    out["levels"] = levels
    out["achieved_level"] = achieved
    out["claim"] = claim
    out["_gate"] = {"violations": violations, "downgraded": bool(violations)}
    return out
