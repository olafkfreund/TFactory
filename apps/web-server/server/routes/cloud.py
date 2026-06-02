"""Cloud assessment portal route (#133/#140).

Serves the latest cloud assessment artifacts (report + Mermaid diagram +
structured JSON) the cloud task-write (#138) produces, so the portal can render
them in a Cloud Assessment view.

    GET /api/cloud/assessment  → { present, json, reportMarkdown, diagramMermaid }

Reads from ``~/.tfactory/cloud-assessments/latest/`` (override with
``TFACTORY_CLOUD_ASSESSMENT_DIR``). This is the portal's read side; the executor
(#138) writes the same artifact shape into a task's ``findings/``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/cloud", tags=["Cloud"])


def _assessment_dir() -> Path:
    override = os.environ.get("TFACTORY_CLOUD_ASSESSMENT_DIR")
    if override:
        return Path(override)
    return Path.home() / ".tfactory" / "cloud-assessments" / "latest"


@router.get("/assessment", summary="Latest cloud assessment (report + diagram)")
def get_cloud_assessment() -> dict:
    base = _assessment_dir()
    js = base / "cloud_assessment.json"
    md = base / "cloud_assessment.md"
    mmd = base / "diagrams" / "cloud_topology.mmd"
    plan = base / "cloud_remediation_plan.md"
    if not js.is_file():
        return {"present": False}
    try:
        data = json.loads(js.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    return {
        "present": True,
        "json": data,
        "reportMarkdown": md.read_text(encoding="utf-8") if md.is_file() else "",
        "diagramMermaid": mmd.read_text(encoding="utf-8") if mmd.is_file() else "",
        "remediationMarkdown": plan.read_text(encoding="utf-8") if plan.is_file() else "",
    }
