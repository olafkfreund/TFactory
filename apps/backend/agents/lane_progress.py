"""Each lane's execution state, derived from the plan and what is on disk (#1259).

``lane_progress`` used to be written once, at the end of evaluation, from the
verdicts' ``lane`` field. So it read ``pending`` for the whole run — with 27
tests executed and 13 junit files on disk — and stayed ``pending`` for good
when the verdicts carried no lane (#1258). Derived instead of stored, it
cannot go stale: the portal recomputes it on every read, and the Evaluator's
end-of-run write goes through the same function.

Per lane that has a subtask in ``test_plan.json``:

- ``error``    — the lane has verdicts and every one is ``stability: error``
- ``executed`` — a verdict resolves to the lane, or a subtask of it has a run
  artifact (``findings/runs/<id>/``, ``findings/_run_artifacts/<stem>/``,
  ``findings/mutants/<id>.*``)
- ``running``  — the Evaluator is running (lanes only run inside it) and none
  of the above yet
- ``pending``  — otherwise

Lanes the plan does not name keep whatever ``status.json`` already says.

A rerun resets ``status`` but leaves ``findings/`` on disk, so evidence is only
believed once this run reached the Evaluator, and only when it is no older
than ``test_plan.json`` — the Planner/Gen-Functional rewrite that file every
run and the Evaluator only reads it, so anything older is a previous run's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.evaluator import _norm_rel, _resolve_subtask

# Statuses in which no lane can have run yet *in this run*.
_PRE_EXECUTION = frozenset(
    {
        "pending",
        "queued",
        "planning",
        "planned",
        "planned_empty",
        "generating",
        "generated",
        "generated_empty",
        "replan_needed",
        "planner_failed",
        "gen_functional_failed",
    }
)


def _read_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _fresh(path: Path, floor: float | None) -> bool:
    try:
        return path.is_file() and (floor is None or path.stat().st_mtime >= floor)
    except OSError:
        return False


def _has_artifact(findings: Path, subtask: dict[str, Any], floor: float | None) -> bool:
    sid = str(subtask.get("id") or "").strip()
    stem = Path(_norm_rel((subtask.get("files_to_create") or [""])[0])).stem
    candidates: list[Path] = []
    if sid:
        runs = findings / "runs" / sid
        if runs.is_dir():
            candidates += list(runs.iterdir())
        candidates += list((findings / "mutants").glob(f"{sid}.*"))
    if stem:
        art = findings / "_run_artifacts" / stem
        candidates += [art / "junit.xml", art / "coverage.xml"]
    return any(_fresh(p, floor) for p in candidates)


def derive_lane_progress(
    spec_dir: Path, verdicts_path: Path | None = None
) -> dict[str, str] | None:
    """Current ``lane_progress`` for *spec_dir*; None when there is nothing to say.

    None (not ``{}``) leaves an existing stored value alone. Never raises:
    unreadable input reads as "no evidence", never as ``error``.
    """
    status = _read_dict(spec_dir / "status.json")
    plan_path = spec_dir / "test_plan.json"
    plan = _read_dict(plan_path)
    try:
        floor: float | None = plan_path.stat().st_mtime if plan else None
    except OSError:
        floor = None
    findings = spec_dir / "findings"
    vpath = verdicts_path or findings / "verdicts.json"

    subtasks = [
        st
        for phase in plan.get("phases") or []
        if isinstance(phase, dict)
        for st in phase.get("subtasks") or []
        if isinstance(st, dict) and str(st.get("lane") or "").strip()
    ]
    planned = {str(st["lane"]).strip().lower() for st in subtasks}
    started = str(status.get("status") or "") not in _PRE_EXECUTION

    derived: dict[str, str] = {}
    if started:
        ran: dict[str, bool] = {}
        verdicts = _read_dict(vpath).get("verdicts") if _fresh(vpath, floor) else None
        for v in verdicts or []:
            if not isinstance(v, dict):
                continue
            lane = str(v.get("lane") or "").strip().lower()
            if not lane:
                lane = str((_resolve_subtask(plan, v) or {}).get("lane") or "")
                lane = lane.strip().lower()
            if not lane:
                continue
            summary = v.get("signals_summary")
            stability = str((summary or {}).get("stability") or "").strip().lower()
            # Only an explicit error means the runner failed; a verdict with no
            # stability was still produced by a lane that ran (#1161).
            ran[lane] = ran.get(lane, False) or stability != "error"
        derived = {lane: "executed" if ok else "error" for lane, ok in ran.items()}
        for st in subtasks:
            lane = str(st["lane"]).strip().lower()
            if lane not in derived and _has_artifact(findings, st, floor):
                derived[lane] = "executed"
    running = status.get("status") == "evaluating"
    for lane in planned - derived.keys():
        derived[lane] = "running" if running else "pending"

    if not derived:
        return None
    stored = status.get("lane_progress")
    progress = dict(stored) if isinstance(stored, dict) else {}
    progress.update(derived)
    return progress
