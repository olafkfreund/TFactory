"""Cloud assessment portal routes (#133/#140/#152).

Surfaces the cloud assessment history the cloud task-write (#138/#150) produces:

    GET /api/cloud/assessments                       — list (newest first)
    GET /api/cloud/assessments/{id}                  — full detail
    GET /api/cloud/assessments/{id}/download/{kind}  — download an artifact
        kind ∈ report.md | remediation.md | issues.json | remediation.pdf
    GET /api/cloud/assessment                        — newest (back-compat)

Backed by ``agents.cloud.store`` (``~/.tfactory/cloud-assessments/<id>/``).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

# Add apps/backend to sys.path so ``import agents.cloud.store`` resolves
# (the canonical pattern used by routes/provider_runtimes.py).
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.cloud import store  # noqa: E402  (after sys.path insert)

router = APIRouter(prefix="/api/cloud", tags=["Cloud"])

_DOWNLOAD_MEDIA = {
    "report.md": "text/markdown",
    "remediation.md": "text/markdown",
    "issues.json": "application/json",
    "remediation.pdf": "application/pdf",
    "report.pdf": "application/pdf",
}


@router.get("/assessments", summary="List cloud assessments (newest first)")
def list_cloud_assessments() -> dict:
    return {"assessments": store.list_assessments()}


@router.get("/assessments/{assessment_id}", summary="One cloud assessment (full)")
def get_cloud_assessment_by_id(assessment_id: str) -> dict:
    data = store.read_assessment(assessment_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assessment not found")
    return data


@router.get(
    "/assessments/{assessment_id}/download/{kind}",
    summary="Download an assessment artifact (md / json / pdf)",
)
def download_cloud_artifact(assessment_id: str, kind: str):
    if kind not in _DOWNLOAD_MEDIA:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown artifact")
    path = store.download_path(assessment_id, kind)
    if path is None or not Path(path).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not available")
    filename = f"cloud-{assessment_id}-{kind}"
    return FileResponse(path, media_type=_DOWNLOAD_MEDIA[kind], filename=filename)


@router.get("/assessment", summary="Newest cloud assessment (back-compat)")
def get_latest_cloud_assessment() -> dict:
    items = store.list_assessments()
    if not items:
        return {"present": False}
    data = store.read_assessment(items[0]["id"])
    return data or {"present": False}
