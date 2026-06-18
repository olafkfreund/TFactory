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

__all__ = ["build_verification_block", "read_verification_block", "DEFAULT_TARGET_LEVEL"]

DEFAULT_TARGET_LEVEL = "VAL-2"  # the ceiling TFactory can reach without a VAL-3 target

# Which VAL level each test lane proves. unit → VAL-1; api/integration/browser
# exercise the assembled app in an ephemeral env → VAL-2. ``mutation`` grades the
# tests, not the SUT's assurance level, so it doesn't map to a VAL level.
_LANE_LEVEL = {
    "unit": "VAL-1",
    "api": "VAL-2",
    "integration": "VAL-2",
    "browser": "VAL-2",
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
) -> dict:
    """Build an honest RFC-0006 verification block from a run's verdicts.

    ``verdicts`` is ``findings/verdicts.json``'s ``verdicts`` list (each entry
    carries a ``lane`` and a ``verdict`` of accept/flag/reject). Returns the
    gate-normalized block: ``{target_level, achieved_level, levels[], claim,
    _gate}`` — ``achieved_level`` is recomputed from the lanes that truly ran, so
    it can never overclaim.
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
        {"level": "VAL-0", "status": "passed" if any_ran else "not_run",
         "reason": None if any_ran else "no tests executed — toolchain/static unproven"}
    )

    # VAL-1 / VAL-2 from the lanes that ran.
    for level in ("VAL-1", "VAL-2"):
        status = _level_status(by_level.get(level, []))
        entry: dict[str, Any] = {"level": level, "status": status}
        if status == "not_run":
            entry["reason"] = (
                f"no {'unit' if level == 'VAL-1' else 'api/integration/browser'} "
                "lane ran in this verify"
            )
        levels.append(entry)

    # VAL-3 (effectful against a real disposable host): no target provisioner yet.
    levels.append(
        {"level": "VAL-3", "status": "not_run",
         "reason": "no disposable sandbox target provisioned (RFC-0006 #75); "
                   "effectful verification against a real host was not run",
         "risk": "behaviour against a real host/cluster is unverified"}
    )

    block = {
        "target_level": target_level,
        # achieved_level is intentionally optimistic here; the gate recomputes the
        # truth from the level statuses below and downgrades any overclaim.
        "achieved_level": target_level,
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
    return build_verification_block(verdicts, target_level=target_level)
