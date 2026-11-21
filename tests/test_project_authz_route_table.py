"""Keep project-bearing routes and MCP tools behind the shared authorization rule."""

from __future__ import annotations

import importlib
import inspect
import sys
import dis
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
from server.pty import access as pty_access  # noqa: E402
from server.routes import (  # noqa: E402  # noqa: E402
    project_authz,
    projects,
    terminal,
    tfactory_tasks,
    mcp_copilot,
)
from server.websockets import events, task_access  # noqa: E402

stdio_router = importlib.import_module("server.mcp_stdio.router")


def _source(obj: Any) -> str:
    return inspect.getsource(obj)


def _has_guard(route: APIRoute | APIWebSocketRoute) -> bool:
    """Follow mounted dependencies and callable guards by object identity."""
    roots = {
        project_authz.require_project_role,
        project_authz.visible_project_ids,
        project_authz.creator_org,
        project_authz.require_task_access,
        task_access.TaskSocketAccess,
        task_access.AllTaskSocketAccess,
        events._load_org_ids,
        terminal._service_only,
        mcp_copilot._check_auth,
    }
    seen: set[int] = set()

    def visit(obj: Any) -> bool:
        if isinstance(obj, (project_authz.ProjectAccessChecker, project_authz.TaskAccessChecker)):
            return True
        if any(obj is root for root in roots):
            return True
        if not inspect.isfunction(obj) or id(obj) in seen:
            return False
        seen.add(id(obj))
        if not obj.__module__.startswith("server."):
            return False
        refs = {**inspect.getclosurevars(obj).globals, **inspect.getclosurevars(obj).nonlocals}
        return any(
            visit(refs[instruction.argval])
            for instruction in dis.get_instructions(obj)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_DEREF"}
            and instruction.argval in refs
        )

    return visit(route.endpoint) or any(visit(dep.call) for dep in route.dependant.dependencies)


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
        "/api/auth/", "/api/orgs", "/api/keys", "/api/git-credentials",
        "/api/test-credentials", "/api/provider-runtimes", "/api/cloud/",
        "/api/portal-tests/", "/api/visual-inspections", "/api/audit/",
        "/api/users/", "/api/notifications", "/api/badges/", "/api/settings",
        "/api/llm-endpoints", "/api/email/", "/api/capabilities", "/api/search",
        "/api/needs-you", "/api/ollama", "/api/claude-code", "/api/mcp/",
        "/api/updates", "/api/memory", "/api/logs", "/api/skills", "/api/health",
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

    # The indirect guards above must continue to call the shared rule.
    for helper in (
        pty_access.project_ok,
        terminal._authorize_path,
        task_access._authorize_project,
        remote_tools._authorize_call,
        stdio_router._authorize_key,
        tfactory_tasks._authorize_spec_request,
        events._may_receive,
    ):
        assert any(
            guard in _source(helper)
            for guard in ("require_project_role", "visible_project_ids", "require_acw_access")
        )
    assert "require_acw_access" in _source(stdio_router._authorized_key)


def test_unguarded_route_fails_route_table() -> None:
    from fastapi import FastAPI  # noqa: PLC0415

    app = FastAPI()

    @app.get("/api/new-project-route")
    async def unguarded() -> dict[str, bool]:
        return {"ok": True}

    route = next(r for r in app.routes if isinstance(r, APIRoute))
    assert route.path.startswith("/api/")
    assert not _has_guard(route)


def test_mcp_registries_use_authorized_dispatch() -> None:
    from agents.tools_pkg.tools import task_control  # noqa: PLC0415

    remote_names = {tool["name"] for tool in remote_tools.get_tool_definitions()}
    assert remote_names == set(remote_tools._SCOPE_FOR_TOOL)
    assert "_authorize_call" in _source(remote_tools.dispatch_tool_call)
    stdio_routes = [route for route in stdio_router.router.routes if isinstance(route, APIRoute)]
    assert len(stdio_routes) >= 16
    assert all(
        any(
            guard in _source(route.endpoint)
            for guard in (
                "require_acw_",
                "_authorized_key",
                "_authorize_key",
                "visible_project_ids",
            )
        )
        for route in stdio_routes
    )
    assert "_locate_local" in _source(task_control.create_task_control_tools)
    assert "_visible_local_projects" in _source(task_control._find_tasks)
    assert {tool.name for tool in task_control.create_task_control_tools()} == {
        "project_list",
        "project_create",
        "task_list",
        "task_status",
        "report_get",
        "task_create_and_run",
        "task_create_from_spec",
        "task_rerun",
    }


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
        ("legacy_disabled", {"id": "default", "is_legacy_token": True}, "pa", "viewer", 404),
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
        check = project_authz.ProjectAccessChecker(role).check(principal, project_id, db)
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
            access = await project_authz.require_project_role(principal, project_id, role, db)
            assert access.role == "viewer"
