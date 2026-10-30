"""Object-level authorization for task-scoped routes (#1450; ported from AIFactory).

Projects are owned by an organization (``org_id`` on the project record).
Access is gated per-org via ``OrgMember`` — except for the **service
principal** (the legacy ``API_TOKEN`` caller and the auth-disabled dev user),
which bypasses per-org checks so Factory-sibling traffic and the local UI keep
working unchanged.

The decision is split in two so it can be unit-tested without a database:

* :func:`check_project_access` — the pure rule (raises ``HTTPException``).
* :class:`TaskAccessChecker` / :func:`require_task_access` — the FastAPI
  dependency that loads the project + membership and applies the rule.

``RBAC_ENFORCE_TASK_ROUTES`` off (default) = shadow mode: the decision is
computed and a would-deny is logged and audited, but the request proceeds.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, cast

from factory_common.logsafe import sanitize_log
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.database.engine import get_db
from server.database.models import OrgMember
from server.routes.organizations import ROLE_LEVELS

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectAccessChecker",
    "TaskAccessChecker",
    "authorize_project_for_user",
    "check_project_access",
    "is_service_principal",
    "require_task_access",
]


def _auth_disabled() -> bool:
    """Whether global auth is off (dev mode). Mirrors TokenAuthMiddleware so the
    authz dependency also no-ops under DISABLE_AUTH."""
    return bool(get_settings().DISABLE_AUTH)


def _enforcing() -> bool:
    return bool(get_settings().RBAC_ENFORCE_TASK_ROUTES)


def is_service_principal(user: dict[str, Any] | None) -> bool:
    """Whether the caller is a machine/dev principal that bypasses per-org authz.

    True for the legacy ``API_TOKEN`` identity and the auth-disabled dev
    principal, both marked ``is_service``; a role claim never counts.
    """
    if not isinstance(user, dict):
        return False
    return bool(user.get("is_service"))


def check_project_access(
    user: dict[str, Any] | None,
    project: dict[str, Any] | None,
    membership: OrgMember | None,
    minimum_role: str = "viewer",
) -> None:
    """Pure authorization rule. Raises ``HTTPException`` on denial; returns None on allow.

    - no user                       → 401
    - service principal              → allow (M2M / dev)
    - project missing                → 404
    - project has no ``org_id``      → 403 (unowned legacy project)
    - API key bound to another org   → 403
    - caller not a member            → 403
    - member role below ``minimum``  → 403
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if is_service_principal(user):
        return

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    org_id = project.get("org_id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project is not assigned to an organization",
        )

    if user.get("api_key_id") and user.get("org_id") != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is scoped to another organization",
        )

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )

    minimum_level = ROLE_LEVELS.get(minimum_role, 0)
    if ROLE_LEVELS.get(membership.role, -1) < minimum_level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This action requires at least '{minimum_role}' role in the "
                f"project's organization."
            ),
        )


async def authorize_project_for_user(
    user: dict[str, Any] | None,
    project_id: str | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> str | None:
    """Authorize ``user`` against ``project_id``'s owning org.

    Raises ``HTTPException`` on denial; returns the project's ``org_id`` (or
    ``None`` for the service principal / auth-disabled paths).
    """
    if _auth_disabled():
        return None
    if is_service_principal(user):
        check_project_access(user, None, None, minimum_role)
        return None

    from server.routes.projects import load_projects  # noqa: PLC0415 - cycle

    project = load_projects().get(project_id) if project_id else None
    org_id = project.get("org_id") if project else None
    membership = None
    if isinstance(user, dict) and user.get("id") and org_id:
        result = await db.execute(
            select(OrgMember).where(
                OrgMember.org_id == org_id,
                OrgMember.user_id == user["id"],
            )
        )
        membership = result.scalar_one_or_none()

    check_project_access(user, project, membership, minimum_role)
    return org_id


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

    async def _authorize(
        self, project_id: str, request: Request, db: AsyncSession
    ) -> dict[str, Any]:
        user = getattr(request.state, "user", None)

        # Auth disabled (dev mode) → allow, like the middleware.
        if _auth_disabled():
            return (
                user
                if isinstance(user, dict)
                else {"id": "default", "role": "admin", "is_service": True}
            )

        enforce = _enforcing()
        try:
            await authorize_project_for_user(user, project_id, db, self.minimum_role)
        except HTTPException as exc:
            uid = user.get("id") if isinstance(user, dict) else None
            if enforce:
                action = "authz.task.denied"
            else:
                action = "authz.task.would_deny"
                logger.warning(
                    "RBAC would deny (shadow): action=%s user=%s project=%s "
                    "status=%s detail=%s",
                    action,
                    sanitize_log(uid),
                    sanitize_log(project_id),
                    exc.status_code,
                    sanitize_log(exc.detail),
                )
            if not enforce or exc.status_code == status.HTTP_403_FORBIDDEN:
                # Hand the request's connection back before the audit write
                # takes a second one; authorization precedes any write.
                with contextlib.suppress(Exception):
                    await db.rollback()
                await _audit_authz_denial(
                    action, user, project_id, self.minimum_role, exc.detail
                )
            if enforce:
                raise
        except Exception as exc:
            if enforce:
                raise
            # Shadow mode must never change a response: fail open.
            logger.warning(
                "RBAC shadow check failed open: project=%s error=%s",
                sanitize_log(project_id),
                sanitize_log(type(exc).__name__),
            )
        return cast("dict[str, Any]", user)

    async def __call__(
        self,
        project_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),  # noqa: B008
    ) -> dict[str, Any]:
        return await self._authorize(project_id, request, db)


class TaskAccessChecker(ProjectAccessChecker):
    """Like :class:`ProjectAccessChecker` but keyed on a ``task_id`` path param
    of the form ``project_id:spec_id`` (the form task/execution routes use)."""

    async def __call__(
        self,
        task_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),  # noqa: B008
    ) -> dict[str, Any]:
        project_id = task_id.split(":", 1)[0] if ":" in task_id else task_id
        return await self._authorize(project_id, request, db)


def require_task_access(minimum_role: str = "viewer") -> TaskAccessChecker:
    """Factory for the task-scoped access dependency (``task_id`` param)."""
    return TaskAccessChecker(minimum_role)
