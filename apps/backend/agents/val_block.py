"""Map a verify run's lane outcomes onto an honest RFC-0006 VAL block (#74).

RFC-0006 grades a completion by *Verification Assurance Level* (VAL-0 static →
VAL-1 unit → VAL-2 ephemeral-integration → VAL-3 real disposable host → VAL-4
prod) and forbids overclaiming. TFactory already runs its lanes in the RFC-0005
Nix sandbox (RFC-0005 #61), so this doesn't re-execute anything — it *attributes*
the lanes that actually ran (from ``findings/verdicts.json``) to VAL levels and
hands the block to the vendored never-overclaim gate, which recomputes
``achieved_level`` from the truth (a failed lower level caps the ceiling; an
absent level is an honest gap, never silently "tested").

VAL-3 (effectful against a real, disposable host) is always ``not_run`` here:
TFactory has no disposable-target provisioner yet (RFC-0006 #75), so a run is
honestly capped at VAL-2 and the gap is surfaced (RFC-0006 #76). This is the
point of the RFC — a VAL-2 result must never look like "done".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.verification_gate import normalize_verification

__all__ = [
    "DEFAULT_TARGET_LEVEL",
    "build_verification_block",
    "read_verification_block",
]

DEFAULT_TARGET_LEVEL = "VAL-2"  # the ceiling TFactory can reach without a VAL-3 target

# Which VAL level each test lane proves. unit → VAL-1; api/integration/browser
# exercise the assembled app in an ephemeral env → VAL-2. ``mutation`` grades the
# tests, not the SUT's assurance level, so it doesn't map to a VAL level.
_LANE_LEVEL = {
    "unit": "VAL-1",
    "api": "VAL-2",
    "integration": "VAL-2",
    "browser": "VAL-2",
    # RFC-0010: the differential/equivalence lane runs the new impl against the
    # legacy reference oracle over the golden corpus in an ephemeral env — same
    # assurance tier as integration. Partial parity yields reject verdicts, which
    # fail VAL-2 and cap achieved_level, so equivalence can never overclaim.
    "equivalence": "VAL-2",
}
_PASS_VERDICTS = {"accept", "flag"}  # flag = accepted-with-note (still ran+passed)


def _level_status(verdicts: list[str]) -> str:
    """passed only if a lane ran and every verdict is a pass; else failed/not_run.

    Conservative: an unknown/non-pass verdict counts as a failure (not silently
    "passed") — honesty over optimism.
    """
    if not verdicts:
        return "not_run"
    return "passed" if all(v in _PASS_VERDICTS for v in verdicts) else "failed"


def build_verification_block(
    verdicts: list[dict[str, Any]] | None,
    *,
    target_level: str = DEFAULT_TARGET_LEVEL,
    val3: Any = None,
) -> dict:
    """Build an honest RFC-0006 verification block from a run's verdicts.

    ``verdicts`` is ``findings/verdicts.json``'s ``verdicts`` list (each entry
    carries a ``lane`` and a ``verdict`` of accept/flag/reject). ``val3`` is an
    optional :class:`agents.disposable_target.Val3Outcome` from a real
    disposable-target run (#75): when it ``ran`` VAL-3 reflects the truth
    (passed/failed); otherwise VAL-3 stays ``not_run`` with the honest reason.
    Returns the gate-normalized block: ``{target_level, achieved_level,
    levels[], claim, _gate}`` — ``achieved_level`` is recomputed from what truly
    ran, so it can never overclaim.
    """
    verdicts = verdicts or []
    by_level: dict[str, list[str]] = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        lane = str(v.get("lane") or "unit").lower()
        level = _LANE_LEVEL.get(lane)
        if level is None:
            continue
        by_level.setdefault(level, []).append(str(v.get("verdict") or "").lower())

    levels: list[dict[str, Any]] = []

    # VAL-0 (static): the generated suite executed at all — the toolchain was
    # present and the code was static-sound enough to import/run. Proven whenever
    # any verdict was produced; otherwise nothing ran.
    any_ran = bool(verdicts)
    levels.append(
        {
            "level": "VAL-0",
            "status": "passed" if any_ran else "not_run",
            "reason": None
            if any_ran
            else "no tests executed — toolchain/static unproven",
        }
    )

    # VAL-1 / VAL-2 from the lanes that ran.
    for level in ("VAL-1", "VAL-2"):
        lane_verdicts = by_level.get(level, [])
        status = _level_status(lane_verdicts)
        entry: dict[str, Any] = {"level": level, "status": status}
        lane_name = "unit" if level == "VAL-1" else "api/integration/browser"
        if status == "not_run":
            entry["reason"] = f"no {lane_name} lane ran in this verify"
        elif status == "failed":
            # A ran-but-failed level MUST carry a reason (the gate flags a gap
            # with no explanation as its own violation, missing_reason:<level>).
            n_fail = sum(1 for v in lane_verdicts if v not in _PASS_VERDICTS)
            entry["reason"] = (
                f"{lane_name} lane: {n_fail}/{len(lane_verdicts)} "
                "test verdict(s) did not pass"
            )
        levels.append(entry)

    # VAL-3 (effectful against a real disposable host, #75): reflects a genuine
    # disposable-target run when one happened; otherwise an honest not_run.
    if val3 is not None and getattr(val3, "ran", False):
        if getattr(val3, "passed", False):
            levels.append(
                {
                    "level": "VAL-3",
                    "status": "passed",
                    "evidence": "ran against a disposable target (auto-torn-down)",
                }
            )
        else:
            levels.append(
                {
                    "level": "VAL-3",
                    "status": "failed",
                    "reason": getattr(val3, "reason", "VAL-3 commands failed"),
                }
            )
    else:
        levels.append(
            {
                "level": "VAL-3",
                "status": "not_run",
                "reason": (getattr(val3, "reason", "") if val3 is not None else "")
                or "no disposable sandbox target provisioned (RFC-0006 #75); "
                "effectful verification against a real host was not run",
                "risk": "behaviour against a real host/cluster is unverified",
            }
        )

    # When a VAL-3 run was attempted, the run targeted VAL-3 — say so; the gate
    # still recomputes achieved_level from the level statuses (a failed VAL-3
    # caps it back down), so this can't overclaim.
    effective_target = (
        "VAL-3" if (val3 is not None and getattr(val3, "ran", False)) else target_level
    )
    block = {
        "target_level": effective_target,
        # achieved_level is intentionally optimistic here; the gate recomputes the
        # truth from the level statuses below and downgrades any overclaim.
        "achieved_level": effective_target,
        "levels": levels,
    }
    return normalize_verification(block)


def read_verification_block(
    spec_dir: Path | str, *, target_level: str = DEFAULT_TARGET_LEVEL
) -> dict:
    """Build the verification block from a spec's ``findings/verdicts.json``.

    Convenience reader so the completion envelope and the PR-comment report can
    share one source of truth. Best-effort: a missing/unreadable verdicts file
    yields the honest "NOT VERIFIED" block (via the gate). Never raises.
    """
    verdicts: list[dict[str, Any]] | None = None
    try:
        path = Path(spec_dir) / "findings" / "verdicts.json"
        if path.exists():
            doc = json.loads(path.read_text())
            verdicts = doc.get("verdicts") if isinstance(doc, dict) else None
    except (OSError, ValueError):
        verdicts = None
    # A VAL-3 disposable-target run (#75) records its outcome here once (the
    # verify path provisions+runs+tears-down a single time). Reading it keeps
    # this a pure function — no provisioning, no double-run. Absent → not_run.
    val3 = None
    try:
        v3path = Path(spec_dir) / "findings" / "val3_outcome.json"
        if v3path.exists():
            from agents.disposable_target import Val3Outcome

            data = json.loads(v3path.read_text())
            if isinstance(data, dict):
                val3 = Val3Outcome(
                    ran=bool(data.get("ran")),
                    passed=bool(data.get("passed")),
                    reason=str(data.get("reason") or ""),
                )
    except (OSError, ValueError):
        val3 = None
    return build_verification_block(verdicts, target_level=target_level, val3=val3)
