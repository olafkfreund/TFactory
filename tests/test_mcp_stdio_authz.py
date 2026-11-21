"""Object-level RBAC tests for stdio-MCP task proxy routes (#1458)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

PROJECTS = {"p1": {"org_id": "A"}, "pB": {"org_id": "B"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """mcp_stdio router on SQLite: org A has viewer-a and member-a."""
    import server.mcp_stdio.router  # noqa: F401
    import server.routes.tasks as tasks_mod
    from server.config import get_settings
    from server.database.models import Base, Organization, OrgMember, User
    from server.mcp_remote.auth import AuthenticatedKey
    from server.mcp_stdio.auth import MCP_READ_SCOPE, TASK_WRITE_SCOPE
    from server.routes.execution import RunningTasksResponse
    from server.routes.tasks import Task, TaskList
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    proxy_router_mod = sys.modules["server.mcp_stdio.router"]

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("server.database.engine.async_session_factory", session_local)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "member-a", "owner"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            db.add(Organization(id="A", name="a", slug="a", owner_id="owner"))
            await db.flush()
            db.add(OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            db.add(OrgMember(org_id="A", user_id="member-a", role="member"))
            db.add(OrgMember(org_id="A", user_id="owner", role="owner"))
            await db.commit()

    asyncio.run(_init())

    app = FastAPI()
    app.include_router(proxy_router_mod.router)
    from server.routes import execution as execution_mod
    from server.routes import files as files_mod
    from server.routes import git as git_mod
    from server.routes import github as github_mod
    from server.routes import handback as handback_mod
    from server.routes import mcp_copilot as mcp_copilot_mod
    from server.routes import projects as projects_mod
    from server.routes import specs as specs_mod
    from server.routes import terminal as terminal_mod
    from server.routes import tfactory_tasks as tfactory_tasks_mod
    from server.routes import coverage as coverage_mod
    from server.websockets import terminal as terminal_ws_mod

    app.include_router(projects_mod.router, prefix="/api/projects")
    app.include_router(files_mod.router, prefix="/api/files")
    app.include_router(git_mod.router, prefix="/api/git")
    app.include_router(terminal_mod.router, prefix="/api/terminals")
    app.include_router(execution_mod.router, prefix="/api/tasks")
    app.include_router(github_mod.router, prefix="/api/github")
    app.include_router(coverage_mod.router)
    app.include_router(handback_mod.router, prefix="/api/handback")
    app.include_router(mcp_copilot_mod.router)
    app.include_router(terminal_ws_mod.router)
    app.include_router(specs_mod.router)
    app.include_router(tfactory_tasks_mod.router, prefix="/api/tfactory/tasks")
    current_principal = {"value": None}

    @app.middleware("http")
    async def _test_principal(request, call_next):
        request.state.user = current_principal["value"]
        return await call_next(request)

    async def _db():
        async with session_local() as session:
            yield session

    # Override the router's own reference: other tests reload
    # server.database.engine, so a fresh import is a different function.
    app.dependency_overrides[proxy_router_mod.get_db] = _db

    settings = get_settings()
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    monkeypatch.setattr(settings, "RBAC_ENFORCE_TASK_ROUTES", False)
    monkeypatch.setattr(settings, "LEGACY_SHARED_TOKEN_FULL_ACCESS", True)
    monkeypatch.setattr(settings, "API_TOKEN", "legacy-admin-secret")
    registered = {pid: dict(data) for pid, data in PROJECTS.items()}
    for pid, data in registered.items():
        project_path = tmp_path / pid
        project_path.mkdir()
        data["path"] = str(project_path)
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: registered)

    audits: list[dict] = []

    async def _audit(**kw):
        audits.append(kw)

    monkeypatch.setattr("server.services.audit_service.log_audit_event_bg", _audit)
    monkeypatch.setattr(proxy_router_mod, "log_audit_event_bg", _audit)

    calls: list[str] = []

    async def _stop_task(task_id: str):
        calls.append(task_id)
        return {"stopped": task_id}

    async def _get_task_logs(task_id: str, tail: int = 100):
        calls.append(f"logs:{task_id}:{tail}")
        return {"logs": task_id}

    async def _get_worktree_diff(task_id: str):
        calls.append(f"diff:{task_id}")
        return {"diff": task_id}

    async def _list_tasks(project_id: str | None = None, status: str | None = None):
        tasks = [
            Task(
                id=f"{pid}:s1",
                spec_id="s1",
                project_id=pid,
                title=pid,
                description=pid,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            for pid in ("p1", "pB")
        ]
        return TaskList(tasks=tasks, total=len(tasks))

    async def _get_running_tasks():
        return RunningTasksResponse(tasks=["p1:s1", "pB:s1"], count=2)

    monkeypatch.setattr("server.routes.execution.stop_task", _stop_task)
    monkeypatch.setattr(tasks_mod, "get_task_logs", _get_task_logs, raising=False)
    monkeypatch.setattr(
        tasks_mod, "get_worktree_diff", _get_worktree_diff, raising=False
    )
    monkeypatch.setattr("server.routes.tasks.list_tasks", _list_tasks)
    monkeypatch.setattr("server.routes.execution.get_running_tasks", _get_running_tasks)

    current_key: dict[str, AuthenticatedKey] = {}

    async def _authenticate(_header: str):
        return current_key["key"]

    monkeypatch.setattr(
        "server.mcp_stdio.auth.mcp_remote_auth.authenticate", _authenticate
    )

    class Env:
        pass

    e = Env()
    e.settings = settings
    e.app = app
    e.db_dependency = _db
    e.registered = registered
    e.current_principal = current_principal
    e.audits = audits
    e.calls = calls
    e.client = TestClient(app)
    e.monkeypatch = monkeypatch

    def set_key(user_id: str, org_id: str = "A", key_id: str = "k1", scopes=None):
        current_key["key"] = AuthenticatedKey(
            key_id=key_id,
            scopes=frozenset(scopes or {MCP_READ_SCOPE, TASK_WRITE_SCOPE}),
            user_id=user_id,
            org_id=org_id,
        )

    e.set_key = set_key
    with e.client:
        yield e
    asyncio.run(engine.dispose())


def test_cross_org_key_denied_even_with_flag_off(env):
    env.set_key(user_id="member-a", org_id="B")
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer acw_org_b"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Project not found"
    assert env.calls == []
    assert env.audits == []


def test_same_org_member_key_calls_handler(env):
    env.set_key(user_id="member-a", org_id="A")
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer acw_org_a"},
    )
    assert r.status_code == 200
    assert env.calls == ["p1:s1"]
    assert [a["action"] for a in env.audits] == ["mcp.task.stop"]


def test_viewer_key_write_is_denied_and_audited(env):
    env.set_key(user_id="viewer-a", org_id="A")
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer acw_viewer_a"},
    )
    assert r.status_code == 403
    assert env.calls == []
    assert [a["action"] for a in env.audits] == ["authz.task.denied"]


def test_merge_scope_still_requires_admin_grant(env):
    from server.mcp_stdio.auth import TASK_MERGE_SCOPE

    env.set_key(user_id="member-a", scopes={TASK_MERGE_SCOPE})
    response = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/worktree/merge",
        headers={"Authorization": "Bearer acw_member"},
    )
    assert response.status_code == 403, response.json()
    assert env.calls == []


def test_legacy_admin_token_calls_handler(env):
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer legacy-admin-secret"},
    )
    assert r.status_code == 200
    assert env.calls == ["p1:s1"]
    assert [a["action"] for a in env.audits] == ["mcp.task.stop"]


def test_legacy_token_bypass_can_be_disabled(env):
    env.settings.LEGACY_SHARED_TOKEN_FULL_ACCESS = False
    response = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer legacy-admin-secret"},
    )
    assert response.status_code == 404
    assert env.calls == []


def test_project_role_intersects_team_grant_and_token_scope(env):
    from server.routes.project_authz import check_project_access

    principal = {
        "id": "owner",
        "api_key_id": "k1",
        "org_id": "A",
        "scopes": ["mcp:read"],
    }
    project = {"id": "p1", "org_id": "A"}
    owner = SimpleNamespace(role="owner")
    assert check_project_access(principal, project, owner).role == "viewer"
    with pytest.raises(HTTPException) as denied:
        check_project_access(principal, project, owner, "member")
    assert denied.value.status_code == 403

    with pytest.raises(HTTPException) as invisible:
        check_project_access(principal, {"id": "pB", "org_id": "B"}, owner)
    assert invisible.value.status_code == 404

    env.settings.LEGACY_SHARED_TOKEN_FULL_ACCESS = False
    with pytest.raises(HTTPException) as legacy:
        check_project_access({"id": "default", "is_legacy_token": True}, project, owner)
    assert legacy.value.status_code == 404


def test_project_lookup_failure_fails_closed_for_org_key(env):
    def _boom():
        raise RuntimeError("projects.json unreadable")

    env.monkeypatch.setattr("server.routes.projects.load_projects", _boom)
    env.set_key(user_id="member-a", org_id="A")
    headers = {"Authorization": "Bearer acw_org_a"}
    with pytest.raises(RuntimeError, match="projects.json unreadable"):
        env.client.post("/api/mcp-stdio/tasks/p1:s1/stop", headers=headers)
    with pytest.raises(RuntimeError, match="projects.json unreadable"):
        env.client.get("/api/mcp-stdio/projects", headers=headers)
    assert env.calls == []


def test_membership_lookup_failure_rolls_back_and_fails_closed(env):
    from server.routes.project_authz import require_project_role

    class BrokenDb:
        rolled_back = False

        async def execute(self, _statement):
            raise RuntimeError("database unavailable")

        async def rollback(self):
            self.rolled_back = True

    db = BrokenDb()
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            require_project_role({"id": "member-a", "org_id": "A"}, "p1", "viewer", db)
        )
    assert db.rolled_back


@pytest.mark.parametrize("task_id", ["nope:s1", "s1"])
def test_org_key_on_unknown_project_denied_even_with_flag_off(env, task_id):
    # get_worktree_diff once scanned every project for a bare/unknown-prefix spec.
    env.set_key(user_id="viewer-a", org_id="A")
    r = env.client.get(
        f"/api/mcp-stdio/tasks/{task_id}/worktree/diff",
        headers={"Authorization": "Bearer acw_viewer_a"},
    )
    assert r.status_code == 404
    assert env.calls == []
    assert env.audits == []


@pytest.mark.parametrize("suffix", ["logs", "worktree/diff"])
def test_cross_org_key_denied_on_reads_even_with_flag_off(env, suffix):
    env.set_key(user_id="viewer-a", org_id="B")
    r = env.client.get(
        f"/api/mcp-stdio/tasks/p1:s1/{suffix}",
        headers={"Authorization": "Bearer acw_org_b"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Project not found"
    assert env.calls == []
    assert env.audits == []


@pytest.mark.parametrize("suffix", ["logs", "worktree/diff"])
def test_same_org_viewer_key_reads_allowed(env, suffix):
    env.set_key(user_id="viewer-a", org_id="A")
    r = env.client.get(
        f"/api/mcp-stdio/tasks/p1:s1/{suffix}",
        headers={"Authorization": "Bearer acw_viewer_a"},
    )
    assert r.status_code == 200
    assert len(env.calls) == 1
    assert env.audits == []


def test_list_routes_filter_foreign_org_for_scoped_key(env):
    env.set_key(user_id="viewer-a", org_id="B")
    headers = {"Authorization": "Bearer acw_org_b"}

    projects = env.client.get("/api/mcp-stdio/projects", headers=headers).json()
    assert [p["id"] for p in projects] == []

    tasks = env.client.get("/api/mcp-stdio/tasks", headers=headers).json()
    assert ([t["project_id"] for t in tasks["tasks"]], tasks["total"]) == ([], 0)

    running = env.client.get("/api/mcp-stdio/tasks/running", headers=headers).json()
    assert (running["tasks"], running["count"]) == ([], 0)


def test_list_routes_legacy_token_sees_all(env):
    headers = {"Authorization": "Bearer legacy-admin-secret"}

    projects = env.client.get("/api/mcp-stdio/projects", headers=headers).json()
    assert [p["id"] for p in projects] == ["p1", "pB"]

    tasks = env.client.get("/api/mcp-stdio/tasks", headers=headers).json()
    assert ([t["project_id"] for t in tasks["tasks"]], tasks["total"]) == (
        ["p1", "pB"],
        2,
    )

    running = env.client.get("/api/mcp-stdio/tasks/running", headers=headers).json()
    assert (running["tasks"], running["count"]) == (["p1:s1", "pB:s1"], 2)


def test_registration_requires_member_on_rest_and_stdio(env, tmp_path):
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    body = {"path": str(tmp_path / "new")}
    assert env.client.post("/api/projects", json=body).status_code == 403
    env.set_key("viewer-a", scopes={"project:write"})
    assert env.client.post(
        "/api/mcp-stdio/projects", json=body,
        headers={"Authorization": "Bearer acw_viewer"},
    ).status_code == 403
    assert not (tmp_path / "new").exists()


def test_registration_hides_foreign_checkout_and_nested_path(env, tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    env.registered["pB"]["path"] = str(foreign)
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    for target in (foreign, foreign / "nested"):
        response = env.client.post("/api/projects", json={"path": str(target)})
        assert (response.status_code, response.json()["detail"]) == (404, "Project not found")
    assert not (foreign / "nested").exists()


def test_file_routes_reject_env_and_resolved_foreign_symlink(env, tmp_path):
    own = tmp_path / "own"
    foreign = tmp_path / "foreign"
    (own / ".tfactory").mkdir(parents=True)
    foreign.mkdir()
    (own / ".tfactory" / ".env").write_text("redacted")
    (foreign / "note.txt").write_text("foreign")
    (own / "link").symlink_to(foreign, target_is_directory=True)
    env.registered["p1"]["path"] = str(own)
    env.registered["pB"]["path"] = str(foreign)
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    assert env.client.get("/api/files/p1/read", params={"path": ".tfactory/.env"}).status_code == 404
    assert env.client.get("/api/files/read", params={"path": str(own / "link" / "note.txt")}).status_code == 404
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    assert env.client.put("/api/files/p1/write", params={"path": ".tfactory/.env"}, json={"content": "changed"}).status_code == 404
    assert env.client.delete("/api/files/p1/delete", params={"path": "."}).status_code == 400
    assert (own / ".tfactory" / ".env").read_text() == "redacted"


def test_viewer_project_list_redacts_stored_tokens(env, tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    env.registered["p1"].update(path=str(own), settings={"gitToken": "redacted", "githubToken": "redacted"})
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    response = env.client.get("/api/projects")
    assert response.status_code == 200
    assert "gitToken" not in response.json()[0]["settings"]
    assert "githubToken" not in response.json()[0]["settings"]


def test_terminal_creation_requires_project_admin(env, tmp_path):
    from server.pty.access import project_id_for_path

    own = tmp_path / "own"
    own.mkdir()
    env.registered["p1"]["path"] = str(own)
    assert project_id_for_path(str(own)) == "p1"
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    response = env.client.post("/api/terminals", json={"cwd": str(own)})
    assert response.status_code == 403, response.json()


def test_local_unregistered_terminal_rest_and_websocket(env, tmp_path, monkeypatch):
    from server.routes import terminal as terminal_mod
    from server.websockets import terminal as terminal_ws_mod

    env.settings.DISABLE_AUTH = True
    session = SimpleNamespace(
        id="local", owner_id=None, project_id=None, cols=80, rows=24, _closed=True,
        is_alive=lambda: True,
        to_dict=lambda: {
            "id": "local", "cwd": str(tmp_path), "shell": "/bin/sh",
            "cols": 80, "rows": 24, "created_at": "2026-01-01T00:00:00",
            "is_alive": True,
        },
    )
    manager = SimpleNamespace(
        get_session=lambda _id: session,
        create_session=lambda **_kwargs: session,
    )
    monkeypatch.setattr(terminal_mod, "get_pty_manager", lambda: manager)
    monkeypatch.setattr(terminal_ws_mod, "get_pty_manager", lambda: manager)
    response = env.client.post("/api/terminals", json={"cwd": str(tmp_path)})
    assert response.status_code == 201
    with env.client.websocket_connect("/ws/terminal/local") as ws:
        assert ws.receive_json()["type"] == "connected"


def test_stdio_bare_status_does_not_search_hidden_task(env, tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    hidden = tmp_path / "workspaces" / "pB" / "specs" / "p1"
    hidden.mkdir(parents=True)
    (hidden / "status.json").write_text('{"status":"running"}')
    env.set_key("member-a")
    response = env.client.get(
        "/api/mcp-stdio/tasks/p1/status",
        headers={"Authorization": "Bearer acw_member"},
    )
    assert response.status_code == 404


def test_stdio_bare_status_uses_visible_project_when_hidden_status_matches(env, tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    visible = tmp_path / "workspaces" / "p1" / "specs" / "same"
    hidden = tmp_path / "workspaces" / "pB" / "specs" / "same"
    visible.mkdir(parents=True)
    hidden.mkdir(parents=True)
    (hidden / "status.json").write_text('{"status":"running"}')
    env.set_key("member-a")
    response = env.client.get(
        "/api/mcp-stdio/tasks/same/status",
        headers={"Authorization": "Bearer acw_member"},
    )
    assert response.status_code == 200
    assert response.json()["is_running"] is False


def test_rest_bare_status_has_same_missing_response_for_hidden_and_unknown(env, tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    hidden = tmp_path / "workspaces" / "pB" / "specs" / "hidden"
    hidden.mkdir(parents=True)
    (hidden / "status.json").write_text('{"status":"running"}')
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    hidden_response = env.client.get("/api/tasks/hidden/status")
    unknown_response = env.client.get("/api/tasks/unknown/status")
    assert (hidden_response.status_code, hidden_response.json()) == (
        unknown_response.status_code, unknown_response.json()
    ) == (404, {"detail": "Task not found"})


def test_path_project_id_wins_over_query_project_id(env):
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    response = env.client.get("/api/projects/pB", params={"project_id": "p1"})
    assert (response.status_code, response.json()) == (404, {"detail": "Project not found"})


def test_host_github_routes_filter_server_identity(env, monkeypatch):
    from server.routes import github

    env.registered["p1"].update(path="/tmp/p1", clonedFrom="https://github.com/a/own.git")
    env.registered["pB"].update(path="/tmp/pB", clonedFrom="https://github.com/b/hidden.git")

    def _gh(_args, **_kwargs):
        return {"success": True, "output": '[{"nameWithOwner":"a/own"},{"nameWithOwner":"b/hidden"}]'}

    monkeypatch.setattr(github, "run_gh_command", _gh)
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    repos = env.client.get("/api/github/repos")
    assert repos.status_code == 200
    assert [repo["fullName"] for repo in repos.json()["data"]["repos"]] == ["a/own"]
    assert env.client.get("/api/github/branches", params={"repo": "b/hidden"}).status_code == 404


def test_coverage_unknown_and_hidden_repo_share_404(env):
    env.registered["pB"].update(path="/tmp/pB", clonedFrom="https://github.com/b/hidden.git")
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    body = {"sha": "abcdef0", "coverage_pct": 75.0}
    hidden = env.client.post("/api/coverage", json={**body, "repo": "b/hidden"})
    unknown = env.client.post("/api/coverage", json={**body, "repo": "x/unknown"})
    assert (hidden.status_code, hidden.json()) == (unknown.status_code, unknown.json()) == (
        404, {"detail": "Project not found"}
    )


def test_remote_mcp_internal_route_uses_scoped_caller_with_legacy_off(env, tmp_path, monkeypatch):
    from server.main import app as main_app
    from server.database.engine import get_db
    from server.mcp_remote.auth import AuthenticatedKey
    from server.mcp_remote.tools import _call_internal

    own = tmp_path / "own"
    own.mkdir()
    env.registered["p1"]["path"] = str(own)
    main_app.dependency_overrides[get_db] = env.db_dependency
    env.settings.LEGACY_SHARED_TOKEN_FULL_ACCESS = False
    key = AuthenticatedKey(
        key_id="k1", scopes=frozenset({"mcp:read"}), user_id="member-a", org_id="A"
    )
    try:
        projects = asyncio.run(_call_internal("GET", "/api/projects", key))
        assert [project["id"] for project in projects] == ["p1"]
    finally:
        main_app.dependency_overrides.pop(get_db, None)


def test_handback_rejects_unregistered_disk_status(env, tmp_path, monkeypatch):
    from server.routes import handback

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    status = tmp_path / "workspaces" / "orphan" / "specs" / "s1" / "status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"status":"failed"}')
    env.registered.clear()
    monkeypatch.setattr(
        handback, "get_settings",
        lambda: SimpleNamespace(INBOUND_HANDBACK_ENABLED=True, INBOUND_HANDBACK_SECRET="test"),
    )
    response = env.client.post(
        "/api/handback/aifactory-complete",
        json={"project_id": "orphan", "spec_id": "s1"},
        headers={"X-TFactory-Handback-Token": "test"},
    )
    assert response.status_code == 404


def test_ingest_does_not_reuse_foreign_registered_clone(env, tmp_path, monkeypatch):
    from server.services import project_workspace_service

    clone_root = tmp_path / "clones"
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(clone_root))
    env.registered["pB"]["path"] = str(clone_root / "b-hidden")
    monkeypatch.setattr(
        project_workspace_service, "clone_or_update",
        AsyncMock(side_effect=AssertionError("clone must not run")),
    )
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    response = env.client.post(
        "/api/specs/ingest",
        json={
            "project_id": "new", "spec_id": "s1", "spec_text": "Build it",
            "git_url": "https://github.com/b/hidden.git",
        },
    )
    assert (response.status_code, response.json()) == (404, {"detail": "Project not found"})


def test_pr_attachment_rejects_other_repository(env):
    env.registered["p1"]["clonedFrom"] = "https://github.com/a/own.git"
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    response = env.client.post(
        "/api/specs/p1/s1/pr",
        json={"pr_number": 1, "repo_slug": "b/hidden"},
    )
    assert (response.status_code, response.json()) == (404, {"detail": "Project not found"})


def test_accepted_test_merge_rejects_other_checkout(env, tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    spec_dir = tmp_path / "workspaces" / "p1" / "specs" / "s1"
    (spec_dir / "findings").mkdir(parents=True)
    (spec_dir / "tests").mkdir()
    (spec_dir / "tests" / "test_x.py").write_text("assert True")
    (spec_dir / "status.json").write_text('{"status":"triaged"}')
    (spec_dir / "findings" / "verdicts.json").write_text(
        '{"verdicts":[{"verdict":"accept","test_file":"tests/test_x.py"}]}'
    )
    env.current_principal["value"] = {"id": "owner", "org_id": "A"}
    response = env.client.post(
        "/api/tfactory/tasks/s1/merge",
        params={"project_id": "p1"},
        json={"dry_run": False, "repo_dir": str(tmp_path / "foreign"), "target_branch": "feature"},
    )
    assert (response.status_code, response.json()) == (404, {"detail": "Project not found"})


def test_visible_member_gets_403_for_admin_merge(env, tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    spec_dir = tmp_path / "workspaces" / "p1" / "specs" / "s1"
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text('{"status":"triaged"}')
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    response = env.client.post(
        "/api/tfactory/tasks/s1/merge", params={"project_id": "p1"}, json={}
    )
    assert response.status_code == 403


def test_scan_and_discovery_require_member_before_listing_paths(env, tmp_path, monkeypatch):
    from server.routes import files, projects

    monkeypatch.setattr(files, "browse_roots", lambda: [tmp_path])
    monkeypatch.setattr(projects, "browse_roots", lambda: [tmp_path])
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    scan = env.client.post("/api/projects/scan", json={"basePath": str(tmp_path)})
    discover = env.client.get("/api/files/discover", params={"base_path": str(tmp_path)})
    assert scan.status_code == discover.status_code == 403


def test_git_init_unregistered_path_requires_member(env, tmp_path):
    env.current_principal["value"] = {"id": "viewer-a", "org_id": "A"}
    response = env.client.post("/api/git/init", json={"path": str(tmp_path)})
    assert response.status_code == 403
    assert not (tmp_path / ".git").exists()


def test_copilot_mcp_credential_works_with_legacy_bypass_off(env, monkeypatch):
    from server.routes import mcp_copilot

    env.settings.LEGACY_SHARED_TOKEN_FULL_ACCESS = False
    monkeypatch.setenv("COPILOT_MCP_TFACTORY_TOKEN", "copilot-test")
    monkeypatch.setitem(mcp_copilot._TOOL_DISPATCH, "tfactory_get_spec", lambda _args: {"ok": True})
    response = env.client.post(
        "/mcp",
        headers={"Authorization": "Bearer copilot-test"},
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tfactory_get_spec", "arguments": {"task_id": "p1:s1"}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False


def test_ingest_hidden_and_unknown_project_have_same_404(env):
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    body = {"spec_id": "s1", "spec_text": "Build it"}
    hidden = env.client.post("/api/specs/ingest", json={**body, "project_id": "pB"})
    unknown = env.client.post("/api/specs/ingest", json={**body, "project_id": "missing"})
    assert (hidden.status_code, hidden.json()) == (unknown.status_code, unknown.json()) == (
        404, {"detail": "Project not found"}
    )


def test_ownerless_terminal_is_available_only_to_project_admin(env, monkeypatch):
    from server.routes import terminal

    session = SimpleNamespace(
        owner_id=None, project_id="p1",
        to_dict=lambda: {
            "id": "ownerless", "cwd": env.registered["p1"]["path"],
            "shell": "/bin/sh", "cols": 80, "rows": 24,
            "created_at": "2026-01-01T00:00:00", "is_alive": True,
        },
    )
    monkeypatch.setattr(terminal, "get_pty_manager", lambda: SimpleNamespace(get_session=lambda _id: session))
    env.current_principal["value"] = {"id": "member-a", "org_id": "A"}
    assert env.client.get("/api/terminals/ownerless").status_code == 404
    env.current_principal["value"] = {"id": "owner", "org_id": "A"}
    assert env.client.get("/api/terminals/ownerless").status_code == 200
