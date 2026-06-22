"""Regression portal route — RFC-0018 #489 (part 2).

Serves the per-project regression read-model (run history, latest diff, coverage
trend, quarantine) the portal surface (#489 part 3) renders:

    GET /api/projects/{project_id}/regression

Backed by ``agents.regression.project_regression_summary`` over the project's
run store at ``<workspace_root>/<project_id>/regression``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

# Make apps/backend importable for the regression read-model.
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402  (after sys.path insert)
    project_regression_summary,
    regression_dir,
)

from ..services.project_workspace_service import workspace_root  # noqa: E402

router = APIRouter(prefix="/api/projects", tags=["Regression"])

# project_id is interpolated into a filesystem path — constrain it to a safe
# slug so a crafted id can't traverse out of the workspace root.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@router.get("/{project_id}/regression")
def get_regression_summary(project_id: str) -> dict[str, Any]:
    """Return the regression read-model for *project_id* (empty-but-valid if none)."""
    if not _ID_RE.match(project_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid project_id",
        )
    reg = regression_dir(workspace_root(), project_id)
    return project_regression_summary(reg)
