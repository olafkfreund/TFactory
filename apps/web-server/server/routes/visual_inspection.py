"""Visual Inspection portal routes (#170 / P4 #174).

Surfaces the visual-inspection run history the packager + store produce:

    GET /api/visual-inspections                       — list (newest first)
    GET /api/visual-inspections/{id}                  — full detail
    GET /api/visual-inspections/{id}/download/{kind}  — download an artifact
        kind ∈ report.md | correction-plan.md | issues.json | meta.json
             | report.pdf | correction-plan.pdf

Backed by ``agents.visual_inspection.store`` (``~/.tfactory/visual-inspections/<id>/``).
Mirrors ``routes/cloud.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.visual_inspection import store  # noqa: E402

router = APIRouter(prefix="/api/visual-inspections", tags=["Visual Inspection"])

_DOWNLOAD_MEDIA = {
    "report.md": "text/markdown",
    "correction-plan.md": "text/markdown",
    "issues.json": "application/json",
    "meta.json": "application/json",
    "report.pdf": "application/pdf",
    "correction-plan.pdf": "application/pdf",
}


@router.get("", summary="List visual inspection runs (newest first)")
def list_visual_inspections() -> dict:
    return {"runs": store.list_runs()}


@router.get("/{run_id}", summary="One visual inspection run (full)")
def get_visual_inspection(run_id: str) -> dict:
    data = store.read_run(run_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    return data


@router.get("/{run_id}/download/{kind}", summary="Download a run artifact")
def download_visual_artifact(run_id: str, kind: str):
    if kind not in _DOWNLOAD_MEDIA:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unknown artifact")
    path = store.download_path(run_id, kind)
    if path is None or not Path(path).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="artifact not available")
    return FileResponse(
        path, media_type=_DOWNLOAD_MEDIA[kind], filename=f"visual-{run_id}-{kind}"
    )


import re as _re  # noqa: E402

# Inline-image filename guard: a flat screenshot name, no path separators / traversal.
_SCREENSHOT_NAME = _re.compile(r"^[A-Za-z0-9._-]+\.(png|jpg|jpeg|webp)$")


@router.get("/{run_id}/screenshots/{name}", summary="Serve a run screenshot inline")
def get_visual_screenshot(run_id: str, name: str):
    """Serve a screenshot from a run's ``screenshots/`` dir so the report's
    ``![](screenshots/x.png)`` images render in the Visual Reports tab. The
    filename is allow-listed (no separators/traversal) and the run dir comes from
    the store's validated resolver."""
    if not _SCREENSHOT_NAME.match(name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="invalid screenshot name"
        )
    run_dir = store._safe_dir(run_id)  # validates run_id; None if absent
    if run_dir is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    path = run_dir / "screenshots" / name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="screenshot not found")
    media = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return FileResponse(path, media_type=media)
