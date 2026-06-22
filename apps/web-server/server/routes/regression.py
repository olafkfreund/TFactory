"""Regression portal route — RFC-0018 #489 (read) + #488 part 3 (on-demand run).

    GET  /api/projects/{project_id}/regression       — the read-model
    POST /api/projects/{project_id}/regression/run    — kick a run on demand

Read-model backed by ``agents.regression.project_regression_summary``; the
on-demand run reuses the shared ``agents.regression.run_for_project`` trigger
over ``<workspace_root>/<project_id>``.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

# Make apps/backend importable for the regression read-model.
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402  (after sys.path insert)
    ProjectScheduleConfig,
    project_regression_summary,
    regression_dir,
    run_for_project,
)
from agents.regression.cli import now_run_id  # noqa: E402

from ..services.project_workspace_service import workspace_root  # noqa: E402

router = APIRouter(prefix="/api/projects", tags=["Regression"])

# project_id is interpolated into a filesystem path — constrain it to a safe
# slug so a crafted id can't traverse out of the workspace root.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _require_valid_id(project_id: str) -> None:
    if not _ID_RE.match(project_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid project_id",
        )


@router.get("/{project_id}/regression")
def get_regression_summary(project_id: str) -> dict[str, Any]:
    """Return the regression read-model for *project_id* (empty-but-valid if none)."""
    _require_valid_id(project_id)
    reg = regression_dir(workspace_root(), project_id)
    return project_regression_summary(reg)


@router.post("/{project_id}/regression/run", status_code=status.HTTP_202_ACCEPTED)
def trigger_regression_run(
    project_id: str, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Kick an on-demand regression run for *project_id*; returns its run_id.

    Runs in a background task on this pod (the unattended/scaled path is the
    nightly CronJob, RFC-0018 #488 part 2). The run re-executes the project's
    persisted corpus on the Nix-Job substrate at ``<workspace_root>/<project>``.
    """
    _require_valid_id(project_id)
    ws = workspace_root()
    config = ProjectScheduleConfig(
        project_id=project_id,
        repo_root=ws / project_id,
        workspace_root=ws,
    )
    # Generate the timestamp here so the response can return the run_id that the
    # backgrounded run_for_project will produce (it derives the id from ``now``).
    now = datetime.now(UTC)
    run_id, _ = now_run_id(now)
    background_tasks.add_task(run_for_project, config, now=now)
    return {"run_id": run_id, "status": "scheduled"}
