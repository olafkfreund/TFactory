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

# Pin ``agents.planner`` into ``sys.modules`` at startup (this route module is
# imported when the app boots). A request-time *fresh* import
# (``from agents.planner import schedule_planner`` inside
# ``create_spec_ingest_workspace``) was intermittently raising ImportError in
# the long-lived server process — the import resolves cleanly at startup but
# not always mid-request — which silently left every ingested spec at
# status=pending with ``planner_scheduled: false`` (TFactory #347). Importing
# here once, at boot, turns that lazy import into a fast sys.modules cache hit.
# Guarded so a minimal venv without the agent SDK can still load the route.
try:
    import agents.planner  # noqa: F401
except Exception:  # pragma: no cover — SDK-less env: lazy import will report it
    pass

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
    source_branch: str | None = Field(
        default=None,
        description=(
            "AIFactory build branch to fetch + check out into the project workspace "
            "before testing, so tests run against the ACTUAL built code (#96). When "
            "omitted, tests run against whatever is currently checked out."
        ),
    )
    contract: dict | None = Field(
        default=None,
        description=(
            "Full signed RFC-0002 Task Contract (the AIFactory implementation_plan). "
            "When present its `tfactory` block (lanes/frameworks/ac_to_code_map) is "
            "the AUTHORITATIVE test profile; persisted to context/task_contract.json. "
            "Absent → tests are inferred from spec_text."
        ),
    )


@router.post("/ingest", summary="Create a TFactory task from a raw spec (no AIFactory branch)")
async def ingest_spec(req: SpecIngestRequest) -> dict:
    # Imported lazily so the route module loads even in environments where the
    # backend package isn't importable until runtime path setup.
    from agents.tools_pkg.tools.task_control import create_spec_ingest_workspace

    # Resolve the project from the web-server's project store (the same source
    # /api/projects uses), by id OR name. The agent-tools file store
    # (~/.tfactory/projects.json) can be empty/diverged from this one, which
    # 404'd every AIFactory→TFactory handoff (#517). AIFactory sends the project
    # *name*; accept either.
    from .projects import load_projects

    projects = load_projects()  # {id: project_data}
    resolved_id = req.project_id
    entry = projects.get(req.project_id)
    if entry is None:
        for pid, data in projects.items():
            if data.get("name") == req.project_id:
                entry, resolved_id = data, pid
                break
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"unknown project_id: {req.project_id!r}",
        )

    try:
        result = create_spec_ingest_workspace(
            project_id=resolved_id,
            spec_id=req.spec_id,
            spec_text=req.spec_text,
            fmt=req.format,
            target_paths=req.target_paths or [],
            project_root=entry.get("path") or entry.get("root_path") or ".",
            contract=req.contract,
            source_branch=req.source_branch,
        )
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"task_id": req.spec_id, "project_id": req.project_id, **result}
