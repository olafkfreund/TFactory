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

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Request as FastAPIRequest
from sqlalchemy.ext.asyncio import AsyncSession

from server.database.engine import get_db
from server.routes.project_authz import (
    ProjectAccessChecker,
    _audit_authz_denial,
)

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


def _key_projects(key: AuthenticatedKey | _LegacyAdminKey) -> dict[str, Any] | None:
    """The project map for an org-scoped key; None when the key has no org to check.

    Fails closed (503): an unreadable registry must not let an org key through.
    """
    if isinstance(key, _LegacyAdminKey) or not key.org_id:
        return None
    from server.routes.projects import load_projects  # noqa: PLC0415

    try:
        projects: dict[str, Any] = load_projects()
        return projects
    except Exception:  # noqa: BLE001 - any registry error fails closed
        raise HTTPException(503, "Project registry unavailable") from None


def _foreign_ids(key: AuthenticatedKey, projects: dict[str, Any]) -> set[str]:
    return {
        pid
        for pid, p in projects.items()
        if isinstance(p, dict) and p.get("org_id") and p.get("org_id") != key.org_id
    }


def _foreign_projects(key: AuthenticatedKey | _LegacyAdminKey) -> set[str]:
    projects = _key_projects(key)
    if projects is None or isinstance(key, _LegacyAdminKey):
        return set()
    return _foreign_ids(key, projects)


async def _authorize_key(
    key: AuthenticatedKey | _LegacyAdminKey,
    project_id: str,
    db: AsyncSession,
    minimum_role: str = "member",
) -> None:
    if isinstance(key, _LegacyAdminKey):
        user = {"id": "default", "is_service": True}
    else:
        user = {
            "id": key.user_id,
            "role": "user",
            "api_key_id": key.key_id,
            "org_id": key.org_id,
        }
        # Not behind the flag: an org-A key must never reach org B, nor an
        # unknown project (a handler may resolve it elsewhere).
        projects = _key_projects(key)
        if projects is not None and project_id not in projects:
            await _audit_authz_denial(
                "authz.task.denied", user, project_id, minimum_role, "Project not found"
            )
            raise HTTPException(404, "Project not found")
        if projects is not None and project_id in _foreign_ids(key, projects):
            detail = "This API key is scoped to another organization"
            await _audit_authz_denial(
                "authz.task.denied", user, project_id, minimum_role, detail
            )
            raise HTTPException(403, detail)

    await ProjectAccessChecker(minimum_role).check(user, project_id, db)


def _authorized_key(
    scope: str,
    minimum_role: str = "member",
) -> Callable[..., Awaitable[AuthenticatedKey | _LegacyAdminKey]]:
    """``require_acw_scope`` plus the project-org check for the task the
    route acts on (``task_id`` path param, else the ``project_id`` query)."""
    scoped = require_acw_scope(scope)

    async def _dep(
        request: FastAPIRequest,
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> AuthenticatedKey | _LegacyAdminKey:
        key: AuthenticatedKey | _LegacyAdminKey = await scoped(
            request
        )  # 401/403 before any project lookup
        task_id = request.path_params.get("task_id")
        project_id = (
            task_id.split(":", 1)[0]
            if task_id
            else request.query_params.get("project_id", "")
        )
        await _authorize_key(key, project_id, db, minimum_role)
        return key

    return _dep


@router.get("/projects")
async def proxy_list_projects(key=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from ..routes.projects import list_projects

    foreign = _foreign_projects(key)
    return [p for p in await list_projects() if p.get("id") not in foreign]


@router.get("/tasks")
async def proxy_list_tasks(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    from server.routes.tasks import TaskList, list_tasks

    foreign = _foreign_projects(key)
    res = await list_tasks(project_id=project_id, status=status)
    tasks = [t for t in res.tasks if t.project_id not in foreign]
    return TaskList(tasks=tasks, total=len(tasks))


@router.get("/tasks/running")
async def proxy_get_running_tasks(key=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from server.routes.execution import RunningTasksResponse, get_running_tasks

    foreign = _foreign_projects(key)
    res = await get_running_tasks()
    tasks = [t for t in res.tasks if t.split(":", 1)[0] not in foreign]
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
):
    from ..routes.projects import ProjectCreate, add_project

    body = await _read_json_body(request)
    result = await add_project(ProjectCreate(**body))
    # ``add_project`` returns the project dict with an ``id`` field.
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
    key=Depends(_authorized_key(TASK_MERGE_SCOPE)),
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
    key=Depends(_authorized_key(TASK_MERGE_SCOPE)),
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
