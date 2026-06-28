"""Regression run store — RFC-0018 #483 (Phase 1).

Persists :class:`RegressionRun` records, one JSON file per run, under a
per-project regression directory::

    <workspace_root>/<project_id>/regression/
        <run_id>.json        # immutable run record
        latest.json          # {"run_id": "..."} pointer to the newest run
        baseline.json        # {"run_id": "..."} pointer to the comparison base

Mirrors ``flaky_history``: an injected directory seam (so unit tests never
touch real workspace state) and atomic write-temp-then-rename persistence.
The directory is the seam — callers resolve it via :func:`regression_dir`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.regression._io import write_json

from .models import RegressionRun

_LATEST = "latest.json"
_BASELINE = "baseline.json"


def regression_dir(workspace_root: Path, project_id: str) -> Path:
    """Return the per-project regression directory (not created)."""
    return Path(workspace_root) / project_id / "regression"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_run(reg_dir: Path, run: RegressionRun, *, set_latest: bool = True) -> Path:
    """Persist *run* as ``<run_id>.json`` and (by default) move ``latest``.

    Returns the path the run was written to. The first run saved also becomes
    the baseline if none is set yet, so the very next run has something to
    diff against.
    """
    path = reg_dir / f"{run.run_id}.json"
    write_json(path, run.to_dict())
    if set_latest:
        write_json(reg_dir / _LATEST, {"run_id": run.run_id})
    if _read_json(reg_dir / _BASELINE) is None:
        write_json(reg_dir / _BASELINE, {"run_id": run.run_id})
    return path


def load_run(reg_dir: Path, run_id: str) -> RegressionRun | None:
    data = _read_json(reg_dir / f"{run_id}.json")
    return RegressionRun.from_dict(data) if data else None


def _pointer(reg_dir: Path, name: str) -> RegressionRun | None:
    ptr = _read_json(reg_dir / name)
    if not ptr or "run_id" not in ptr:
        return None
    return load_run(reg_dir, str(ptr["run_id"]))


def load_latest(reg_dir: Path) -> RegressionRun | None:
    """Return the most-recently saved run, or None."""
    return _pointer(reg_dir, _LATEST)


def load_baseline(reg_dir: Path) -> RegressionRun | None:
    """Return the run new runs are diffed against, or None."""
    return _pointer(reg_dir, _BASELINE)


def set_baseline(reg_dir: Path, run_id: str) -> None:
    """Pin *run_id* as the baseline for future diffs (operator action)."""
    if not (reg_dir / f"{run_id}.json").exists():
        raise FileNotFoundError(f"no such regression run: {run_id}")
    write_json(reg_dir / _BASELINE, {"run_id": run_id})


def list_runs(reg_dir: Path) -> list[str]:
    """Return all stored run_ids (sorted), excluding pointer/aux/report files.

    Run files are ``<run_id>.json``. The same directory also holds pointer
    files, the quarantine + coverage-trend stores, and ``<run_id>-report.json``
    reports — none of which are runs, so they must be filtered out.
    """
    if not reg_dir.exists():
        return []
    # Sibling non-run JSON files written into the regression dir.
    aux = {_LATEST, _BASELINE, "quarantine.json", "coverage_trend.json"}
    return sorted(
        p.stem
        for p in reg_dir.glob("*.json")
        if p.name not in aux
        and not p.name.endswith(".tmp")
        and not p.name.endswith("-report.json")
    )
