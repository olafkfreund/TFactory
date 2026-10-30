"""Object-level RBAC tests for stdio-MCP task proxy routes (#1458)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
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
            await db.commit()

    asyncio.run(_init())

    app = FastAPI()
    app.include_router(proxy_router_mod.router)

    async def _db():
        async with session_local() as session:
            yield session

    # Override the router's own reference: other tests reload
    # server.database.engine, so a fresh import is a different function.
    app.dependency_overrides[proxy_router_mod.get_db] = _db

    settings = get_settings()
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    monkeypatch.setattr(settings, "RBAC_ENFORCE_TASK_ROUTES", False)
    monkeypatch.setattr(settings, "API_TOKEN", "legacy-admin-secret")
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: PROJECTS)

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

    async def _list_projects():
        return [{"id": "p1"}, {"id": "pB"}]

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
    monkeypatch.setattr("server.routes.projects.list_projects", _list_projects)
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
    e.audits = audits
    e.calls = calls
    e.client = TestClient(app)
    e.monkeypatch = monkeypatch

    def set_key(user_id: str, org_id: str = "A", key_id: str = "k1"):
        current_key["key"] = AuthenticatedKey(
            key_id=key_id,
            scopes=frozenset({MCP_READ_SCOPE, TASK_WRITE_SCOPE}),
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
    assert r.status_code == 403
    assert r.json()["detail"] == "This API key is scoped to another organization"
    assert env.calls == []
    assert [a["action"] for a in env.audits] == ["authz.task.denied"]


def test_same_org_member_key_calls_handler(env):
    env.set_key(user_id="member-a", org_id="A")
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer acw_org_a"},
    )
    assert r.status_code == 200
    assert env.calls == ["p1:s1"]
    assert [a["action"] for a in env.audits] == ["mcp.task.stop"]


def test_viewer_key_in_shadow_audits_would_deny_and_calls_handler(env):
    env.set_key(user_id="viewer-a", org_id="A")
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer acw_viewer_a"},
    )
    assert r.status_code == 200
    assert env.calls == ["p1:s1"]
    assert [a["action"] for a in env.audits] == [
        "authz.task.would_deny",
        "mcp.task.stop",
    ]


def test_legacy_admin_token_calls_handler(env):
    r = env.client.post(
        "/api/mcp-stdio/tasks/p1:s1/stop",
        headers={"Authorization": "Bearer legacy-admin-secret"},
    )
    assert r.status_code == 200
    assert env.calls == ["p1:s1"]
    assert [a["action"] for a in env.audits] == ["mcp.task.stop"]


def test_project_lookup_failure_fails_closed_for_org_key(env):
    def _boom():
        raise RuntimeError("projects.json unreadable")

    env.monkeypatch.setattr("server.routes.projects.load_projects", _boom)
    env.set_key(user_id="member-a", org_id="A")
    headers = {"Authorization": "Bearer acw_org_a"}
    assert (
        env.client.post("/api/mcp-stdio/tasks/p1:s1/stop", headers=headers).status_code
        == 503
    )
    assert env.client.get("/api/mcp-stdio/projects", headers=headers).status_code == 503
    assert env.calls == []


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
    assert [a["action"] for a in env.audits] == ["authz.task.denied"]


@pytest.mark.parametrize("suffix", ["logs", "worktree/diff"])
def test_cross_org_key_denied_on_reads_even_with_flag_off(env, suffix):
    env.set_key(user_id="viewer-a", org_id="B")
    r = env.client.get(
        f"/api/mcp-stdio/tasks/p1:s1/{suffix}",
        headers={"Authorization": "Bearer acw_org_b"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "This API key is scoped to another organization"
    assert env.calls == []
    assert [a["action"] for a in env.audits] == ["authz.task.denied"]
    assert env.audits[0]["details"]["minimum_role"] == "viewer"


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
    assert [p["id"] for p in projects] == ["pB"]

    tasks = env.client.get("/api/mcp-stdio/tasks", headers=headers).json()
    assert ([t["project_id"] for t in tasks["tasks"]], tasks["total"]) == (["pB"], 1)

    running = env.client.get("/api/mcp-stdio/tasks/running", headers=headers).json()
    assert (running["tasks"], running["count"]) == (["pB:s1"], 1)


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
