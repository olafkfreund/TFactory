"""Regression portal read-model — RFC-0018 #489 (part 1).

A pure read-only view over a project's regression store (runs, latest report,
coverage trend, quarantine) assembled into one JSON-friendly dict for the
portal. The web-server route (#489 part 2) serves this; the React surface
(#489 part 3) renders it. Keeping the read-model pure makes it testable
without the web stack.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .coverage_trend import coverage_trend_path, load_trend
from .quarantine import load_quarantine, quarantine_path
from .store import list_runs, load_baseline, load_latest, load_run


def _latest_report(reg_dir: Path, run_id: str) -> dict[str, Any] | None:
    """Read the persisted ``<run_id>-report.json`` if present."""
    path = reg_dir / f"{run_id}-report.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def project_regression_summary(reg_dir: Path) -> dict[str, Any]:
    """Portal read-model for one project's regression history.

    Returns an empty-but-valid shape when the project has no runs yet, so the
    portal can render "no regression runs" without special-casing.
    """
    reg_dir = Path(reg_dir)
    latest = load_latest(reg_dir)
    baseline = load_baseline(reg_dir)

    history: list[dict[str, Any]] = []
    for run_id in list_runs(reg_dir):
        run = load_run(reg_dir, run_id)
        if run is None:
            continue
        history.append(
            {
                "run_id": run.run_id,
                "ran_at": run.ran_at,
                "commit": run.commit,
                "totals": run.totals,
                "coverage_pct": run.coverage_pct,
            }
        )

    report = _latest_report(reg_dir, latest.run_id) if latest else None
    quarantined = [
        {"test_id": tid, **entry.to_dict()}
        for tid, entry in sorted(load_quarantine(quarantine_path(reg_dir)).items())
    ]

    return {
        "latest_run_id": latest.run_id if latest else None,
        "baseline_run_id": baseline.run_id if baseline else None,
        "has_regressions": bool(report["diff"]["has_regressions"])
        if report and "diff" in report
        else None,
        "runs": history,
        "latest": latest.to_dict() if latest else None,
        "latest_diff": report.get("diff") if report else None,
        "latest_drift": report.get("drift") if report else None,
        "coverage_trend": [
            p.to_dict() for p in load_trend(coverage_trend_path(reg_dir))
        ],
        "quarantined": quarantined,
    }
