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

PROJECTS = {"p1": {"org_id": "A"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """mcp_stdio router on SQLite: org A has viewer-a and member-a."""
    import server.mcp_stdio.router  # noqa: F401
    from server.config import get_settings
    from server.database.engine import get_db
    from server.database.models import Base, Organization, OrgMember, User
    from server.mcp_remote.auth import AuthenticatedKey
    from server.mcp_stdio.auth import TASK_WRITE_SCOPE
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

    app.dependency_overrides[get_db] = _db

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

    monkeypatch.setattr("server.routes.execution.stop_task", _stop_task)

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
            scopes=frozenset({TASK_WRITE_SCOPE}),
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
