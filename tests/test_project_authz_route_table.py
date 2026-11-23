"""Keep project-bearing routes and MCP tools behind the shared authorization rule."""

from __future__ import annotations

import dis
import importlib
import inspect
import sys
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute, APIWebSocketRoute

_WEB_SERVER = Path(__file__).resolve().parents[1] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.main import create_app  # noqa: E402
from server.mcp_remote import tools as remote_tools  # noqa: E402
from server.routes import (  # noqa: E402  # noqa: E402
    mcp_copilot,
    project_authz,
    projects,
    terminal,
)
from server.websockets import events, task_access  # noqa: E402

stdio_router = importlib.import_module("server.mcp_stdio.router")


def _has_guard(route: APIRoute | APIWebSocketRoute) -> bool:
    """Follow mounted dependencies and callable guards across module reloads."""
    roots = {
        project_authz.require_project_role,
        project_authz.creator_org,
        project_authz.require_task_access,
        task_access.TaskSocketAccess,
        task_access.AllTaskSocketAccess,
        events._load_org_ids,
        terminal._service_only,
        mcp_copilot._check_auth,
    }
    root_names = {(root.__module__, root.__qualname__) for root in roots}
    seen: set[int] = set()

    def visit(obj: Any) -> bool:
        if (type(obj).__module__, type(obj).__qualname__) in {
            ("server.routes.project_authz", "ProjectAccessChecker"),
            ("server.routes.project_authz", "TaskAccessChecker"),
        }:
            return True
        if (
            getattr(obj, "__module__", None),
            getattr(obj, "__qualname__", None),
        ) in root_names:
            return True
        if not inspect.isfunction(obj) or id(obj) in seen:
            return False
        seen.add(id(obj))
        if not obj.__module__.startswith("server."):
            return False
        refs = {
            **inspect.getclosurevars(obj).globals,
            **inspect.getclosurevars(obj).nonlocals,
        }
        instructions = list(dis.get_instructions(obj))

        def used_filter(start: int) -> bool:
            call = next(
                (
                    i
                    for i in range(start + 1, len(instructions))
                    if instructions[i].opname == "CALL"
                ),
                None,
            )
            if call is None:
                return False
            outcome = next(
                (
                    i
                    for i in range(call + 1, len(instructions))
                    if instructions[i].opname
                    in {"STORE_FAST", "STORE_DEREF", "POP_TOP", "RETURN_VALUE"}
                ),
                None,
            )
            if outcome is None:
                return False
            op = instructions[outcome]
            if op.opname == "RETURN_VALUE":
                return True
            return op.opname.startswith("STORE_") and any(
                later.opname in {"LOAD_FAST", "LOAD_DEREF"}
                and later.argval == op.argval
                for later in instructions[outcome + 1 :]
            )

        return any(
            (
                used_filter(index)
                if (
                    getattr(refs[instruction.argval], "__module__", None),
                    getattr(refs[instruction.argval], "__qualname__", None),
                )
                == ("server.routes.project_authz", "visible_project_ids")
                else visit(refs[instruction.argval])
            )
            for index, instruction in enumerate(instructions)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_DEREF"}
            and instruction.argval in refs
            and next(
                (
                    later.opname
                    for later in instructions[index + 1 :]
                    if later.opname
                    in {"CALL", "STORE_FAST", "STORE_DEREF", "RETURN_VALUE"}
                ),
                None,
            )
            == "CALL"
        )

    return visit(route.endpoint) or any(
        visit(dep.call) for dep in route.dependant.dependencies
    )


def test_project_route_table() -> None:
    """A new project route must declare a guard or join this reviewed allowlist."""
    exempt = {
        # No project is selected; these either return host capability data or
        # perform no project operation.
        ("POST", "/api/tasks/worktree/detect-tools"),
        ("GET", "/api/terminals/session-dates"),
        ("POST", "/api/terminals/restore"),
        ("POST", "/api/terminals/restore-date"),
        ("POST", "/api/terminals/generate-name"),
        ("GET", "/api/terminals/sessions/{date}"),
        ("POST", "/api/terminals/{terminal_id}/invoke-claude"),
        ("POST", "/api/terminals/{terminal_id}/resume-claude"),
        ("GET", "/api/github/user"),
        ("GET", "/api/github/orgs"),
        ("GET", "/api/github/cli/check"),
        ("POST", "/api/github/cli/install"),
        ("GET", "/api/github/auth/check"),
        ("POST", "/api/github/auth/start"),
        ("GET", "/api/github/auth/status"),
        ("GET", "/api/github/token"),
        # Dedicated shared-secret webhook; registration is checked in its body.
        ("POST", "/api/handback/aifactory-complete"),
    }
    # Host and account APIs have no project target. Every other API route must
    # expose a mounted guard, including new prefixes.
    host_only = (
        "/api/auth/",
        "/api/orgs",
        "/api/keys",
        "/api/git-credentials",
        "/api/test-credentials",
        "/api/provider-runtimes",
        "/api/cloud/",
        "/api/portal-tests/",
        "/api/visual-inspections",
        "/api/audit/",
        "/api/users/",
        "/api/notifications",
        "/api/badges/",
        "/api/settings",
        "/api/llm-endpoints",
        "/api/email/",
        "/api/capabilities",
        "/api/search",
        "/api/needs-you",
        "/api/ollama",
        "/api/claude-code",
        "/api/mcp/",
        "/api/updates",
        "/api/memory",
        "/api/logs",
        "/api/skills",
        "/api/health",
    )
    routes = [
        route
        for route in create_app().routes
        if isinstance(route, (APIRoute, APIWebSocketRoute))
        and route.path.startswith(("/api/", "/mcp", "/ws/"))
        and not route.path.startswith(host_only)
        and not (
            isinstance(route, APIRoute)
            and all((method, route.path) in exempt for method in route.methods)
        )
    ]
    assert routes
    unguarded = [route.path for route in routes if not _has_guard(route)]
    assert not unguarded, "\n".join(unguarded)


def test_unguarded_route_fails_route_table() -> None:
    from fastapi import FastAPI  # noqa: PLC0415

    app = FastAPI()

    @app.get("/api/new-project-route")
    async def unguarded() -> dict[str, bool]:
        return {"ok": True}

    route = next(r for r in app.routes if isinstance(r, APIRoute))
    assert route.path.startswith("/api/")
    assert not _has_guard(route)

    @app.get("/api/loaded-only")
    async def loaded_only() -> dict[str, bool]:
        unused = project_authz.require_project_role
        return {"ok": bool(unused)}

    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/loaded-only")
    assert not _has_guard(route)

    @app.get("/api/discarded-filter")
    async def discarded_filter() -> dict[str, bool]:
        await project_authz.visible_project_ids({})
        return {"ok": True}

    route = next(
        r for r in app.routes if getattr(r, "path", "") == "/api/discarded-filter"
    )
    assert not _has_guard(route)


def test_sensitive_route_dependency_roles() -> None:
    required = {
        ("GET", "/api/projects/{projectId}/env"): "viewer",
        ("PATCH", "/api/projects/{projectId}/env"): "admin",
        ("PUT", "/api/files/{project_id}/write"): "member",
        ("DELETE", "/api/projects/{project_id}"): "admin",
    }
    checked = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method, route.path)
            if key not in required:
                continue
            roles = [
                dependency.call.minimum_role
                for dependency in route.dependant.dependencies
                if isinstance(dependency.call, project_authz.ProjectAccessChecker)
            ]
            assert (
                roles and max(roles, key=project_authz.ROLE_LEVELS.get) == required[key]
            )
            checked.add(key)
    assert checked == required.keys()


def test_mcp_registries_use_authorized_dispatch() -> None:
    remote_names = {tool["name"] for tool in remote_tools.get_tool_definitions()}
    assert remote_names == set(remote_tools._SCOPE_FOR_TOOL)
    stdio_routes = [
        route for route in stdio_router.router.routes if isinstance(route, APIRoute)
    ]
    assert len(stdio_routes) >= 16


@pytest.mark.parametrize(
    ("case", "principal", "project_id", "role", "expected"),
    [
        ("unknown", {"id": "member", "org_id": "A"}, "missing", "viewer", 404),
        ("non_member", {"id": "outsider", "org_id": "A"}, "pa", "viewer", 404),
        ("insufficient", {"id": "viewer", "org_id": "A"}, "pa", "member", 403),
        (
            "scope_viewer",
            {"id": "owner", "org_id": "A", "api_key_id": "k", "scopes": ["mcp:read"]},
            "pa",
            "viewer",
            200,
        ),
        (
            "scope_ceiling",
            {"id": "owner", "org_id": "A", "api_key_id": "k", "scopes": ["mcp:read"]},
            "pa",
            "member",
            403,
        ),
        (
            "cross_team_service",
            {
                "id": None,
                "org_id": "B",
                "api_key_id": "k",
                "scopes": ["mcp:read"],
                "scoped_service": True,
            },
            "pa",
            "viewer",
            404,
        ),
        (
            "legacy_disabled",
            {"id": "default", "is_legacy_token": True},
            "pa",
            "viewer",
            404,
        ),
        ("local", None, "pa", "member", 200),
    ],
)
@pytest.mark.parametrize("surface", ["rest", "websocket", "mcp"])
@pytest.mark.asyncio
async def test_shared_authz_matrix(  # noqa: PLR0913 - pytest matrix inputs
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    principal: dict[str, Any] | None,
    project_id: str,
    role: str,
    expected: int,
    surface: str,
) -> None:
    settings = SimpleNamespace(
        DISABLE_AUTH=case == "local",
        PROJECTS_BACKEND="json",
        LEGACY_SHARED_TOKEN_FULL_ACCESS=case != "legacy_disabled",
    )
    monkeypatch.setattr(project_authz, "get_settings", lambda: settings)
    monkeypatch.setattr(projects, "load_projects", lambda: {"pa": {"org_id": "A"}})
    monkeypatch.setattr(project_authz, "_load_projects", projects.load_projects)
    members = {"viewer": "viewer", "owner": "owner", "member": "member"}
    db = AsyncMock()
    row = (
        SimpleNamespace(role=members[principal["id"]])
        if principal and principal.get("id") in members
        else None
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: row)
    monkeypatch.setattr(project_authz, "_audit_authz_denial", AsyncMock())

    check: Awaitable[Any]
    if surface == "rest":
        check = project_authz.ProjectAccessChecker(role).check(
            principal, project_id, db
        )
    elif surface == "websocket":

        @asynccontextmanager
        async def session() -> Any:
            yield db

        monkeypatch.setattr(task_access, "async_session_factory", session)
        # Task streams require viewer; member-only socket actions use the
        # shared rule directly with their higher minimum role.
        check = (
            task_access._authorize_project(principal, project_id)
            if role == "viewer"
            else project_authz.require_project_role(principal, project_id, role, db)
        )
    else:
        monkeypatch.setattr(stdio_router, "acw_principal", lambda _key: principal)
        check = stdio_router._authorize_key(object(), project_id, db, role)
    try:
        result = await check
    except HTTPException as exc:
        assert exc.status_code == expected
    else:
        assert expected == 200
        if surface == "rest":
            assert isinstance(result, dict)
        elif surface == "websocket" and role == "member":
            assert isinstance(result, project_authz.EffectiveProjectAccess)
        else:
            assert result is None
        if case == "scope_viewer":
            access = await project_authz.require_project_role(
                principal, project_id, role, db
            )
            assert access.role == "viewer"
