"""Generic spec-ingestion route (WS2 / #40) — the portal "New test from spec" door.

``POST /api/specs/ingest`` accepts a raw acceptance-criteria spec (markdown /
Gherkin ``.feature`` / EARS) and creates a TFactory task **without** an AIFactory
branch, delegating to the backend's
``agents.tools_pkg.tools.task_control.create_spec_ingest_workspace``.

Auth is enforced by the global ``TokenAuthMiddleware`` (this is an ``/api/*``
route); no per-route dependency is needed. Errors map to HTTP codes:
``ValueError`` → 400 (unparseable / no criteria), ``FileExistsError`` → 409
(spec_id collision), unknown project → 404.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/specs", tags=["Spec Ingestion"])


class SpecIngestRequest(BaseModel):
    project_id: str = Field(..., description="Registered project id")
    spec_id: str = Field(..., min_length=1, description="New task/spec id (workspace dir name)")
    spec_text: str = Field(..., min_length=1, description="Raw markdown / Gherkin / EARS spec")
    format: str | None = Field(
        default=None, description="markdown | gherkin | ears (auto-detected when omitted)"
    )
    target_paths: list[str] | None = Field(
        default=None, description="Repo-relative files/modules under test (target-mode)"
    )


@router.post("/ingest", summary="Create a TFactory task from a raw spec (no AIFactory branch)")
async def ingest_spec(req: SpecIngestRequest) -> dict:
    # Imported lazily so the route module loads even in environments where the
    # backend package isn't importable until runtime path setup.
    from agents.tools_pkg.tools.task_control import (
        _load_projects,
        create_spec_ingest_workspace,
    )

    projects = _load_projects()
    entry = next(
        (p for p in projects.get("projects", []) if p.get("id") == req.project_id),
        None,
    )
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"unknown project_id: {req.project_id!r}",
        )

    try:
        result = create_spec_ingest_workspace(
            project_id=req.project_id,
            spec_id=req.spec_id,
            spec_text=req.spec_text,
            fmt=req.format,
            target_paths=req.target_paths or [],
            project_root=entry.get("root_path", "."),
        )
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"task_id": req.spec_id, "project_id": req.project_id, **result}
