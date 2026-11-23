"""Who may use a terminal session (#3740).

A session is bound to its creating user and project. The service principal
(legacy token, auth-disabled dev user) keeps full access; everyone else needs
to own the session and still hold its project. Every check fails closed.
"""

import importlib
import os
from pathlib import Path, PurePath
from typing import Any, cast

from server.database import engine
from server.routes._specpath import contained_path
from server.routes.project_authz import is_service_principal, require_project_role


def _projects() -> dict[str, Any]:
    # The route package imports terminal access while assembling its routers.
    return cast(
        "dict[str, Any]",
        importlib.import_module("server.routes.projects").load_projects(),
    )


async def project_ok(
    user: dict[str, Any] | None, project_id: str | None, role: str = "viewer"
) -> bool:
    """Strict project authorization; denial or lookup failure is False."""
    if project_id is None and is_service_principal(None):
        return True
    try:
        async with engine.async_session_factory() as db:
            await require_project_role(user, project_id, role, db)
    except Exception:  # noqa: BLE001 - denial or lookup failure fails closed
        return False
    return True


async def may_use(
    user: dict[str, Any] | None, session: Any, role: str = "viewer"
) -> bool:
    if user is None:
        return is_service_principal(None) and await project_ok(
            None, session.project_id, role
        )
    if is_service_principal(user):
        return await project_ok(user, session.project_id, role)
    if session.owner_id is None:
        return await project_ok(user, session.project_id, "admin")
    if user.get("id") != session.owner_id:
        return False
    return await project_ok(user, session.project_id, "admin")


def _roots(project: object) -> list[PurePath]:
    """Normalised and resolved forms of a registered project's path, from trusted data."""
    # A repo-only project stores "" (no clone); Path("") would be the CWD.
    if not isinstance(project, dict) or not project.get("path"):
        return []
    raw = str(project["path"])
    try:
        return [PurePath(os.path.normpath(raw)), Path(raw).resolve()]
    except (OSError, RuntimeError, ValueError):
        return [PurePath(os.path.normpath(raw))]


def _inside(target: PurePath, root: PurePath) -> bool:
    return target == root or root in target.parents


def project_id_for_path(path: str | None) -> str | None:
    """Registered project id for a project id or a path inside a project."""
    if not path:
        return None
    projects = _projects()
    if path in projects:
        return path
    if not PurePath(path).is_absolute():
        return None
    try:
        target = contained_path(
            path,
            [root for project in projects.values() for root in _roots(project)],
            "registered projects",
        )
    except ValueError:
        return None
    best: tuple[int, str] | None = None
    ambiguous = False
    for project_id, project in projects.items():
        for root in _roots(project):
            if not _inside(target, root):
                continue
            if best is None or len(root.parts) > best[0]:
                best = (len(root.parts), project_id)
                ambiguous = False
            elif len(root.parts) == best[0] and project_id != best[1]:
                ambiguous = True
    return best[1] if best and not ambiguous else None


def path_in_project(path: str, project_id: str) -> bool:
    if not PurePath(path).is_absolute():
        return False
    target = Path(path).resolve()
    return any(_inside(target, root) for root in _roots(_projects().get(project_id)))
