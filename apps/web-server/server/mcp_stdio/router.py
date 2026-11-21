"""Proxy router for the stdio-MCP control plane (Issue #154).

Re-exposes the 15 operations the stdio MCP client exercises today,
under ``/api/mcp-stdio/*`` — each route gated by ``acw_`` key + scope.

Every handler delegates to the same service the regular REST routes
use. The proxy adds nothing but auth + audit: no URL rewriting, no
payload transformation, no extra hop. If a delegated handler raises
``HTTPException``, it propagates as-is (and no audit row is written).

Why import-and-call instead of HTTP-forwarding to the existing routes:
- One process, no extra hop, no risk of an MCP request looping back
  through ``TokenAuthMiddleware`` which would reject the ``acw_`` key
  on the regular REST surface.
- Same code path, so behavior identity is mechanical.

Audit logging (Epic #50 acceptance criterion #2):
Each write route fires a background ``log_audit_event_bg`` call after
the delegated handler returns successfully. Actions land under the
``mcp.*`` namespace (``mcp.task.start``, ``mcp.project.create``, etc.)
so MCP-initiated mutations stay distinguishable from equivalent
UI-driven actions in the audit log.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi import Request as FastAPIRequest
from sqlalchemy.ext.asyncio import AsyncSession

from server.database.engine import get_db
from server.routes import specs as specs_routes
from server.routes import tfactory_tasks as tfactory_tasks_routes
from server.routes._specpath import safe_component
from server.routes.project_authz import (
    creator_org,
    require_project_role,
    resolve_project_id,
    visible_project_ids,
)
from server.routes.specs import SpecIngestRequest

from ..mcp_remote.auth import AuthenticatedKey
from ..services.audit_service import (
    ACTION_MCP_PROJECT_CREATE,
    ACTION_MCP_TASK_APPROVE_PLAN,
    ACTION_MCP_TASK_CREATE_AND_RUN,
    ACTION_MCP_TASK_CREATE_PR,
    ACTION_MCP_TASK_MERGE,
    ACTION_MCP_TASK_RECOVER,
    ACTION_MCP_TASK_START,
    ACTION_MCP_TASK_STOP,
    log_audit_event_bg,
)
from .auth import (
    MCP_READ_SCOPE,
    PROJECT_WRITE_SCOPE,
    TASK_MERGE_SCOPE,
    TASK_WRITE_SCOPE,
    _LegacyAdminKey,
    acw_principal,
    require_acw_access,
    require_acw_scope,
)

router = APIRouter(prefix="/api/mcp-stdio", tags=["MCP (stdio)"])


async def _read_json_body(request: FastAPIRequest) -> dict:
    """Read request body, return {} if empty."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


async def _audit_mcp_write(
    key: AuthenticatedKey | _LegacyAdminKey,
    action: str,
    resource_type: str,
    resource_id: str | None,
    request: FastAPIRequest,
    details: dict | None = None,
) -> None:
    """Fire-and-forget background audit log entry for an MCP write.

    Uses ``log_audit_event_bg`` (self-managed session) so the route
    handler doesn't need a ``Depends(get_db)`` and so a slow audit
    write doesn't block the response. Failures inside the bg call
    are swallowed by audit_service's try/except wrapper — never
    crashes the route.

    The ``key_id`` from the auth key goes into ``details`` so
    operators can correlate audit rows back to a specific minted
    key (useful when investigating a compromised key).
    """
    enriched_details = dict(details or {})
    enriched_details["mcp_key_id"] = (
        key.key_id if isinstance(key, AuthenticatedKey) else "legacy-admin"
    )
    client_ip = request.client.host if request.client else None
    await log_audit_event_bg(
        user_id=key.user_id,
        org_id=key.org_id if isinstance(key, AuthenticatedKey) else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=enriched_details,
        ip=client_ip,
    )


# =============================================================================
# Read operations — mcp:read
# =============================================================================


async def _authorize_key(
    key: AuthenticatedKey | _LegacyAdminKey,
    project_id: str,
    db: AsyncSession,
    minimum_role: str = "member",
) -> None:
    await require_project_role(acw_principal(key), project_id, minimum_role, db)


def _authorized_key(
    scope: str, minimum_role: str = "member"
) -> Callable[..., Awaitable[AuthenticatedKey | _LegacyAdminKey]]:
    return require_acw_access(scope, minimum_role)


@router.get("/projects")
async def proxy_list_projects(
    key: AuthenticatedKey | _LegacyAdminKey = Depends(require_acw_scope(MCP_READ_SCOPE)),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    from ..routes.projects import list_projects

    allowed = await visible_project_ids(acw_principal(key), db)
    return [p for p in await list_projects() if p.get("id") in allowed]


@router.get("/tasks")
async def proxy_list_tasks(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    from server.routes.tasks import TaskList, list_tasks

    allowed = await visible_project_ids(acw_principal(key), db)
    if project_id is not None:
        await require_project_role(acw_principal(key), project_id, db=db)
    res = await list_tasks(project_id=project_id, status=status)
    tasks = [t for t in res.tasks if t.project_id in allowed]
    return TaskList(tasks=tasks, total=len(tasks))


@router.get("/tasks/running")
async def proxy_get_running_tasks(
    key: AuthenticatedKey | _LegacyAdminKey = Depends(require_acw_scope(MCP_READ_SCOPE)),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> object:
    from server.routes.execution import RunningTasksResponse, get_running_tasks

    allowed = await visible_project_ids(acw_principal(key), db)
    res = await get_running_tasks()
    tasks = [t for t in res.tasks if t.split(":", 1)[0] in allowed]
    return RunningTasksResponse(tasks=tasks, count=len(tasks))


@router.get("/tasks/{task_id}")
async def proxy_get_task(
    task_id: str, _=Depends(_authorized_key(MCP_READ_SCOPE, "viewer"))
):
    from ..routes.tasks import get_task

    return await get_task(task_id)


@router.get("/tasks/{task_id}/status")
async def proxy_get_task_status(
    task_id: str, _=Depends(_authorized_key(MCP_READ_SCOPE, "viewer"))
):
    from ..routes.execution import get_task_status

    return await get_task_status(task_id)


@router.get("/tasks/{task_id}/logs")
async def proxy_get_task_logs(
    task_id: str,
    tail: int = Query(default=100),
    _=Depends(_authorized_key(MCP_READ_SCOPE, "viewer")),
):
    from ..routes.tasks import get_task_logs

    return await get_task_logs(task_id, tail=tail)


@router.get("/tasks/{task_id}/worktree/diff")
async def proxy_get_worktree_diff(
    task_id: str, _=Depends(_authorized_key(MCP_READ_SCOPE, "viewer"))
):
    from ..routes.tasks import get_worktree_diff

    return await get_worktree_diff(task_id)


# =============================================================================
# Project mutation — project:write
# =============================================================================


@router.post("/projects", status_code=201)
async def proxy_add_project(
    request: FastAPIRequest,
    key=Depends(require_acw_scope(PROJECT_WRITE_SCOPE)),
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    from ..routes.projects import ProjectCreate, create_project

    body = await _read_json_body(request)
    org_id = await creator_org(acw_principal(key), db)
    result = await create_project(ProjectCreate(**body), org_id)
    # ``create_project`` returns the project dict with an ``id`` field.
    project_id = result.get("id") if isinstance(result, dict) else None
    await _audit_mcp_write(
        key,
        ACTION_MCP_PROJECT_CREATE,
        "project",
        project_id,
        request,
    )
    return result


# =============================================================================
# Task mutation — task:write
# =============================================================================


@router.post("/tasks/create-and-run")
async def proxy_create_and_run_task(
    request: FastAPIRequest,
    project_id: str = Query(...),
    title: str = Query(...),
    description: str = Query(...),
    key=Depends(_authorized_key(TASK_WRITE_SCOPE)),
):
    from ..routes.execution import StartTaskRequest, create_and_run_task

    body = await _read_json_body(request)
    result = await create_and_run_task(
        project_id, title, description, StartTaskRequest(**body)
    )
    task_id = result.get("task_id") if isinstance(result, dict) else None
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        "task",
        task_id,
        request,
        details={"project_id": project_id, "title": title},
    )
    return result


def _resolve_project(ref: str) -> str | None:
    return resolve_project_id(ref)


_Key = AuthenticatedKey | _LegacyAdminKey
_ReadKey = Annotated[_Key, Depends(require_acw_scope(MCP_READ_SCOPE))]
_WriteKey = Annotated[_Key, Depends(require_acw_scope(TASK_WRITE_SCOPE))]
_Db = Annotated[AsyncSession, Depends(get_db)]
_Tenant = Annotated[str | None, Header(alias="X-Tenant-Id")]


@router.post("/specs/ingest")
async def proxy_ingest_spec(
    req: SpecIngestRequest,
    request: FastAPIRequest,
    key: _WriteKey,
    db: _Db,
    x_tenant_id: _Tenant = None,
) -> dict[str, Any]:
    resolved_id = _resolve_project(req.project_id)
    if resolved_id is not None:
        await _authorize_key(key, resolved_id, db, "member")
    elif not (req.git_url or req.repo):
        raise HTTPException(404, "Project not found")
    request.state.user = acw_principal(key)
    result = await specs_routes.ingest_spec(
        req, x_tenant_id=x_tenant_id, request=request, db=db
    )
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        "task",
        result.get("task_id"),
        request,
        details={"project_id": req.project_id, "mode": "spec_ingest"},
    )
    return result


@router.get("/spec-tasks")
async def proxy_list_spec_tasks(
    key: _ReadKey,
    db: _Db,
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    x_tenant_id: _Tenant = None,
) -> dict[str, Any]:
    resolved_id = None
    if project_id is not None:
        resolved_id = _resolve_project(project_id)
        if resolved_id is None:
            raise HTTPException(404, "Project not found")
        await _authorize_key(key, resolved_id, db, "viewer")
    allowed = await visible_project_ids(acw_principal(key), db)
    rows = [
        row
        for row in tfactory_tasks_routes.list_tasks(x_tenant_id=x_tenant_id)["tasks"]
        if row["project_id"] in allowed
        and (resolved_id is None or row["project_id"] == resolved_id)
        and (status is None or row["status"] == status)
    ]
    return {"tasks": rows, "count": len(rows)}


@router.get("/spec-tasks/{project_id}/{spec_id}")
async def proxy_get_spec_task(
    project_id: str,
    spec_id: str,
    key: _ReadKey,
    db: _Db,
    report: Literal["md", "json"] | None = Query(default=None),
) -> dict[str, Any]:
    resolved_id = _resolve_project(project_id)
    if resolved_id is None:
        raise HTTPException(404, "Project not found")
    await _authorize_key(key, resolved_id, db, "viewer")
    spec_dir = (
        tfactory_tasks_routes._resolve_workspace_root()
        / "workspaces"
        / safe_component(resolved_id)
        / "specs"
        / safe_component(spec_id)
    )
    status_path = spec_dir / "status.json"
    if not status_path.is_file():
        raise HTTPException(404, "Task not found")
    status_doc = tfactory_tasks_routes._read_json(status_path) or {}
    try:
        lane_progress = importlib.import_module("agents.lane_progress")
        live = lane_progress.derive_lane_progress(spec_dir)
    except Exception:  # noqa: BLE001 - keep the stored progress if derivation fails
        live = None
    if live is not None:
        status_doc["lane_progress"] = live
    source_doc = (
        tfactory_tasks_routes._read_json(spec_dir / "context" / "source.json") or {}
    )
    result: dict[str, Any] = {
        "task_id": status_doc.get("task_id") or spec_id,
        "project_id": resolved_id,
        "spec_id": spec_id,
        "status_json": status_doc,
        "source": {
            field: source_doc.get(field)
            for field in (
                "source_sha",
                "repo_slug",
                "source_branch",
                "pr_number",
                "source_ref",
            )
        },
    }
    if report is not None:
        report_path = spec_dir / "findings" / f"triage_report.{report}"
        if not report_path.is_file():
            raise HTTPException(404, "Report not found")
        result["report"] = {"format": report, "body": report_path.read_text()}
    return result


@router.post("/tasks/{task_id}/start")
async def proxy_start_task(
    task_id: str,
    request: FastAPIRequest,
    key=Depends(_authorized_key(TASK_WRITE_SCOPE)),
):
    from ..routes.execution import StartTaskRequest, start_task

    body = await _read_json_body(request)
    result = await start_task(task_id, StartTaskRequest(**body), request)
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_START,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/stop")
async def proxy_stop_task(
    task_id: str,
    request: FastAPIRequest,
    key=Depends(_authorized_key(TASK_WRITE_SCOPE)),
):
    from ..routes.execution import stop_task

    result = await stop_task(task_id)
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_STOP,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/recover")
async def proxy_recover_task(
    task_id: str,
    request: FastAPIRequest,
    key=Depends(_authorized_key(TASK_WRITE_SCOPE)),
):
    from ..routes.execution import RecoverTaskRequest, recover_task

    body = await _read_json_body(request)
    result = await recover_task(task_id, RecoverTaskRequest(**body))
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_RECOVER,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/approve-plan")
async def proxy_approve_plan(
    task_id: str,
    request: FastAPIRequest,
    key=Depends(_authorized_key(TASK_WRITE_SCOPE)),
):
    from ..routes.tasks import ApprovePlanRequest, approve_plan

    body = await _read_json_body(request)
    result = await approve_plan(task_id, ApprovePlanRequest(**body))
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_APPROVE_PLAN,
        "task",
        task_id,
        request,
    )
    return result


# =============================================================================
# PR / merge — task:merge (higher blast radius)
# =============================================================================


@router.post("/tasks/{task_id}/worktree/create-pr")
async def proxy_create_pr(
    task_id: str,
    request: FastAPIRequest,
    key=Depends(_authorized_key(TASK_MERGE_SCOPE, "admin")),
):
    from ..routes.tasks import CreatePRFromTaskOptions, create_pr_from_task

    body = await _read_json_body(request)
    result = await create_pr_from_task(
        task_id, CreatePRFromTaskOptions(**body) if body else None
    )
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_CREATE_PR,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/worktree/merge")
async def proxy_merge_worktree(
    task_id: str,
    request: FastAPIRequest,
    key=Depends(_authorized_key(TASK_MERGE_SCOPE, "admin")),
):
    from ..routes.tasks import WorktreeMergeOptions, merge_worktree

    body = await _read_json_body(request)
    result = await merge_worktree(
        task_id, WorktreeMergeOptions(**body) if body else None
    )
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_MERGE,
        "task",
        task_id,
        request,
    )
    return result
