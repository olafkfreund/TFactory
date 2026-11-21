"""Project authorization shared by REST, WebSocket, and MCP callers."""

from __future__ import annotations

import contextlib
import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from factory_common.logsafe import sanitize_log
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.database.engine import get_db
from server.database.models import OrgMember, Project
from server.routes.organizations import ROLE_LEVELS
from server.services.project_store import get_project_store

from ._specpath import safe_component

logger = logging.getLogger(__name__)
_FORBIDDEN = status.HTTP_403_FORBIDDEN

__all__ = [
    "SCOPE_CEILING",
    "EffectiveProjectAccess",
    "ProjectAccessChecker",
    "TaskAccessChecker",
    "authorize_project_for_user",
    "check_project_access",
    "creator_org",
    "is_service_principal",
    "require_project_role",
    "require_task_access",
    "resolve_project_id",
    "resolve_task_project",
    "visible_project_ids",
]

SCOPE_CEILING = {
    "mcp:read": "viewer",
    "project:write": "member",
    "task:write": "member",
    "plan:write": "member",
    "mcp:write": "member",
    "task:merge": "admin",
    "api:full": "admin",
}


@dataclass(frozen=True)
class EffectiveProjectAccess:
    project_id: str
    org_id: str
    role: str


def _auth_disabled() -> bool:
    """Whether global auth is off (dev mode). Mirrors TokenAuthMiddleware so the
    authz dependency also no-ops under DISABLE_AUTH."""
    return bool(get_settings().DISABLE_AUTH)


def is_service_principal(user: dict[str, Any] | None) -> bool:
    """Whether local mode or the enabled legacy token has host-wide access."""
    return _auth_disabled() or bool(
        isinstance(user, dict)
        and user.get("is_legacy_token") is True
        and get_settings().LEGACY_SHARED_TOKEN_FULL_ACCESS
    )


def check_project_access(  # noqa: PLR0912
    user: dict[str, Any] | None,
    project: dict[str, Any] | None,
    membership: OrgMember | None,
    minimum_role: str = "viewer",
) -> EffectiveProjectAccess:
    """Apply visibility, team-grant, and token-scope rules to a loaded project."""
    if not user and not _auth_disabled():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    user = user or {}
    project_id = str(project["id"])
    if _auth_disabled():
        return EffectiveProjectAccess(project_id, "default", "owner")
    if (
        user.get("is_legacy_token") is True
        and get_settings().LEGACY_SHARED_TOKEN_FULL_ACCESS
    ):
        logger.warning("authz.legacy_token_bypass project=%s", sanitize_log(project_id))
        return EffectiveProjectAccess(
            project_id, str(project.get("org_id") or "default"), "owner"
        )
    if user.get("is_legacy_token") is True:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.get("is_copilot_token") is True:
        return EffectiveProjectAccess(
            project_id, str(project.get("org_id") or "default"), "owner"
        )

    org_id = project.get("org_id")
    if (
        not org_id
        or (user.get("api_key_id") and user.get("org_id") != org_id)
        or (user.get("org_id") and user["org_id"] != org_id)
    ):
        raise HTTPException(status_code=404, detail="Project not found")
    grant = (
        membership.role
        if membership is not None
        else (
            "owner"
            if user.get("scoped_service") and user.get("org_id") == org_id
            else None
        )
    )
    if grant not in ROLE_LEVELS:
        raise HTTPException(status_code=404, detail="Project not found")

    scopes = user.get("scopes")
    if user.get("api_key_id"):
        ceiling = max(
            (
                ROLE_LEVELS[SCOPE_CEILING[scope]]
                for scope in scopes or ()
                if scope in SCOPE_CEILING
            ),
            default=-1,
        )
        if "api:full" in (scopes or ()) and membership is not None and grant == "owner":
            ceiling = ROLE_LEVELS["owner"]
    else:
        ceiling = ROLE_LEVELS["owner"]
    level = min(ROLE_LEVELS[grant], ceiling)
    if level < ROLE_LEVELS["viewer"]:
        raise HTTPException(status_code=404, detail="Project not found")
    if level < ROLE_LEVELS[minimum_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This action requires at least '{minimum_role}' project role",
        )
    role = next(role for role, role_level in ROLE_LEVELS.items() if role_level == level)
    return EffectiveProjectAccess(project_id, str(org_id), role)


async def authorize_project_for_user(
    user: dict[str, Any] | None,
    project_id: str | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> str | None:
    """Compatibility wrapper for callers that only need the owning org."""
    return (await require_project_role(user, project_id, minimum_role, db)).org_id


def _load_projects() -> dict[str, dict[str, Any]]:
    # projects.py imports this module, so resolve its public store helper lazily.
    return cast(
        "dict[str, dict[str, Any]]",
        importlib.import_module("server.routes.projects").load_projects(),
    )


async def require_project_role(
    principal: dict[str, Any] | None,
    project_id: str | None,
    min_role: str = "viewer",
    db: AsyncSession | None = None,
) -> EffectiveProjectAccess:
    """Authorize a project, hiding unknown or invisible projects with 404."""
    if min_role not in ROLE_LEVELS:
        raise ValueError(f"Unknown project role: {min_role}")
    if not principal and not _auth_disabled():
        raise HTTPException(
            401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
        )
    if db is None:
        from server.database.engine import async_session_factory  # noqa: PLC0415

        async with async_session_factory() as session:
            return await require_project_role(principal, project_id, min_role, session)

    try:
        if (get_settings().PROJECTS_BACKEND or "json").strip().lower() == "db":
            org_id = (
                await db.execute(select(Project.org_id).where(Project.id == project_id))
            ).scalar_one_or_none()
            projects = (
                await get_project_store(session=db, org_id=org_id).load_all()
                if org_id
                else {}
            )
            project = (
                {**projects[project_id], "id": project_id, "org_id": org_id}
                if project_id in projects
                else None
            )
        else:
            projects = _load_projects()
            project = (
                {**projects[project_id], "id": project_id}
                if project_id in projects
                else None
            )
        membership = None
        if (
            project
            and principal
            and principal.get("id")
            and project.get("org_id")
            and not _auth_disabled()
        ):
            result = await db.execute(
                select(OrgMember).where(
                    OrgMember.org_id == project["org_id"],
                    OrgMember.user_id == principal["id"],
                )
            )
            membership = result.scalar_one_or_none()
        return check_project_access(principal or {}, project, membership, min_role)
    except HTTPException as exc:
        if exc.status_code == _FORBIDDEN:
            with contextlib.suppress(Exception):
                await db.rollback()
            await _audit_authz_denial(
                "authz.task.denied", principal, project_id, min_role, exc.detail
            )
        raise
    except Exception:
        with contextlib.suppress(Exception):
            await db.rollback()
        raise


async def visible_project_ids(
    principal: dict[str, Any] | None,
    db: AsyncSession | None = None,
    min_role: str = "viewer",
) -> set[str]:
    """Return only registered projects the caller may see at ``min_role``."""
    if db is None:
        from server.database.engine import async_session_factory  # noqa: PLC0415

        async with async_session_factory() as session:
            return await visible_project_ids(principal, session, min_role)
    if (get_settings().PROJECTS_BACKEND or "json").strip().lower() == "db":
        if is_service_principal(principal):
            org_ids = (
                (await db.execute(select(Project.org_id).distinct())).scalars().all()
            )
        elif principal and principal.get("scoped_service"):
            org_ids = [str(principal["org_id"])] if principal.get("org_id") else []
        elif principal and principal.get("id"):
            org_ids = (
                (
                    await db.execute(
                        select(OrgMember.org_id).where(
                            OrgMember.user_id == principal["id"]
                        )
                    )
                )
                .scalars()
                .all()
            )
        else:
            org_ids = []
        projects = {}
        for org_id in filter(None, org_ids):
            projects.update(
                await get_project_store(session=db, org_id=org_id).load_all()
            )
    else:
        projects = _load_projects()
    visible: set[str] = set()
    for project_id in projects:
        try:
            await require_project_role(principal, project_id, min_role, db)
        except HTTPException as exc:
            if exc.status_code not in (403, 404):
                raise
        else:
            visible.add(project_id)
    return visible


def resolve_project_id(ref: str) -> str | None:
    """Resolve an exact registered ID or an unambiguous project name."""
    projects = _load_projects()
    if ref in projects:
        return ref
    matches = [pid for pid, project in projects.items() if project.get("name") == ref]
    return matches[0] if len(matches) == 1 else None


def resolve_task_project(
    task_id: str, visible_project_ids: set[str] | None = None
) -> tuple[str, str]:
    """Resolve a task ID only within the caller's visible registered projects."""
    projects = _load_projects()
    allowed = (
        projects.keys()
        if visible_project_ids is None
        else projects.keys() & visible_project_ids
    )
    if ":" in task_id:
        project_id, spec_id = task_id.split(":", 1)
        safe_component(project_id)
        safe_component(spec_id)
        if project_id in allowed:
            return project_id, spec_id
        raise HTTPException(404, "Task not found")
    spec_id = safe_component(task_id)
    from .tfactory_tasks import _resolve_workspace_root  # noqa: PLC0415

    workspace_root = _resolve_workspace_root() / "workspaces"
    matches = [
        pid
        for pid in allowed
        if (workspace_root / pid / "specs" / spec_id).is_dir()
        or (
            projects[pid].get("path")
            and (Path(projects[pid]["path"]) / ".tfactory" / "specs" / spec_id).is_dir()
        )
    ]
    if len(matches) > 1:
        raise HTTPException(409, "Specify project_id for this task")
    if not matches:
        raise HTTPException(404, "Task not found")
    return matches[0], spec_id


async def _audit_authz_denial(
    action: str,
    user: dict[str, Any] | None,
    project_id: str | None,
    minimum_role: str,
    detail: object,
) -> None:
    """Write an authz audit row on its own committed session.

    Not the request-scoped session: nothing commits it after a 403. Fail-safe:
    the caller's outcome never depends on the audit write.
    """
    try:
        from server.services.audit_service import (  # noqa: PLC0415
            log_audit_event_bg,
        )

        await log_audit_event_bg(
            action=action,
            resource_type="project",
            resource_id=project_id,
            user_id=user.get("id") if isinstance(user, dict) else None,
            details={"detail": str(detail), "minimum_role": minimum_role},
        )
    except Exception:  # noqa: BLE001  # pragma: no cover - audit must not mask the outcome
        logger.debug("authz audit write failed", exc_info=True)


class ProjectAccessChecker:
    """FastAPI dependency: authorize the caller against a project's owning org.

    Reads identity from ``request.state.user`` (set by ``TokenAuthMiddleware``).
    """

    def __init__(self, minimum_role: str = "viewer") -> None:
        self.minimum_role = minimum_role

    async def check(
        self,
        user: dict[str, Any] | None,
        project_id: str | None,
        db: AsyncSession,
    ) -> dict[str, Any]:
        try:
            try:
                await require_project_role(user, project_id, self.minimum_role, db)
            finally:
                # Release the pooled connection before the route handler runs.
                with contextlib.suppress(Exception):
                    await db.rollback()
        except HTTPException:
            raise
        return user if isinstance(user, dict) else {"id": "default", "role": "owner"}

    async def _authorize(
        self, project_id: str | None, request: Request, db: AsyncSession
    ) -> dict[str, Any]:
        return await self.check(getattr(request.state, "user", None), project_id, db)

    async def __call__(
        self,
        request: Request,
        db: AsyncSession = Depends(get_db),  # noqa: B008
    ) -> dict[str, Any]:
        project_id = request.path_params.get("project_id") or request.path_params.get(
            "projectId"
        )
        return await self._authorize(project_id, request, db)


class TaskAccessChecker:
    """Like :class:`ProjectAccessChecker` but keyed on a ``task_id`` path param
    of the form ``project_id:spec_id`` (the form task/execution routes use)."""

    def __init__(self, minimum_role: str = "viewer") -> None:
        self.minimum_role = minimum_role

    async def __call__(
        self,
        task_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),  # noqa: B008
    ) -> dict[str, Any]:
        if ":" in task_id:
            project_id = task_id.split(":", 1)[0]
        else:
            principal = getattr(request.state, "user", None)
            allowed = await visible_project_ids(principal, db)
            project_id = resolve_task_project(task_id, allowed)[0]
        return await ProjectAccessChecker(self.minimum_role)._authorize(
            project_id, request, db
        )


def require_task_access(minimum_role: str = "viewer") -> TaskAccessChecker:
    """Factory for the task-scoped access dependency (``task_id`` param)."""
    return TaskAccessChecker(minimum_role)


async def creator_org(user: dict[str, Any] | None, db: AsyncSession) -> str | None:
    """The org to stamp on a project this principal creates (#1036).

    Authenticated callers need one org where they hold at least member, and
    keys need a project-write scope. Local mode and the enabled legacy service
    token retain the org-less single-user behavior.
    """
    if is_service_principal(user):
        return None
    if not isinstance(user, dict) or user.get("is_legacy_token"):
        raise HTTPException(403, "Project creation requires org membership")
    scopes = set(user.get("scopes") or ())
    if user.get("api_key_id") and not ({"project:write", "api:full"} & scopes):
        raise HTTPException(403, "Project creation requires project:write scope")
    orgs: list[str] = []
    if user.get("id"):
        rows = await db.execute(
            select(OrgMember).where(OrgMember.user_id == user["id"])
        )
        orgs = [
            str(row.org_id)
            for row in rows.scalars().all()
            if ROLE_LEVELS.get(row.role, -1) >= ROLE_LEVELS["member"]
            and (not user.get("org_id") or user["org_id"] == row.org_id)
        ]
    if len(orgs) == 1:
        return orgs[0]
    logger.warning(
        "project.org_unstamped principal=%s orgs=%d",
        sanitize_log(user.get("id")),
        len(orgs),
    )
    raise HTTPException(403, "Project creation requires one org membership with member role")
