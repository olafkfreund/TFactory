"""Object-level org/role authorization tests for remote MCP tools (#1456)."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.database.models import Base, Organization, OrgMember, User  # noqa: E402
from server.mcp_remote.auth import (  # noqa: E402
    MCP_READ_SCOPE,
    MCP_WRITE_SCOPE,
    AuthenticatedKey,
)

tools_mod = importlib.import_module("server.mcp_remote.tools")
project_authz_mod = importlib.import_module("server.routes.project_authz")
projects_mod = importlib.import_module("server.routes.projects")

PROJECTS = {"pa": {"org_id": "A"}, "pb": {"org_id": "B"}}

READ_TASK_TOOLS = (
    "tfactory.get_task",
    "tfactory.get_worktree_diff",
    "tfactory.get_qa_report",
    "tfactory.tail_agent_console",
)
WRITE_TASK_TOOLS = (
    "tfactory.start_task",
    "tfactory.stop_task",
    "tfactory.approve_plan",
    "tfactory.merge_pr",
    "tfactory.reject_plan",
    "tfactory.recover_task",
)
ALL_TASK_TOOLS = (*READ_TASK_TOOLS, *WRITE_TASK_TOOLS)


def _key(user_id: str | None, org_id: str | None = "A") -> AuthenticatedKey:
    return AuthenticatedKey(
        key_id="k1",
        scopes=frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}),
        user_id=user_id,
        org_id=org_id,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Remote MCP tools wired to real SQLite OrgMember rows and real project_authz."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "member-a", "owner"):
                db.add(
                    User(id=uid, email=f"{uid}@x.test", password_hash="x")  # noqa: S106
                )
            await db.flush()
            db.add(Organization(id="A", name="a", slug="a", owner_id="owner"))
            db.add(Organization(id="B", name="b", slug="b", owner_id="owner"))
            await db.flush()
            db.add(OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            db.add(OrgMember(org_id="A", user_id="member-a", role="member"))
            await db.commit()

    asyncio.run(_init())

    async def _db():
        async with session_local() as session:
            yield session

    settings = SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=False)
    monkeypatch.setattr(tools_mod, "get_db", _db, raising=False)
    monkeypatch.setattr(tools_mod, "get_settings", lambda: settings, raising=False)
    monkeypatch.setattr(project_authz_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(tools_mod, "load_projects", lambda: PROJECTS, raising=False)
    monkeypatch.setattr(projects_mod, "load_projects", lambda: PROJECTS)

    async def _fake_call_internal(
        method: str, path: str, _key: AuthenticatedKey, **_kw: object
    ) -> object:
        if path == "/api/projects":
            return [{"id": "pa"}, {"id": "pb"}]
        return {"method": method, "path": path}

    call_internal = AsyncMock(side_effect=_fake_call_internal)
    monkeypatch.setattr(tools_mod, "_call_internal", call_internal)

    yield SimpleNamespace(call_internal=call_internal)
    asyncio.run(engine.dispose())


async def test_org_a_member_denied_every_task_tool_and_list_tasks_on_org_b(env):
    key = _key("member-a", "A")
    for tool_name in ALL_TASK_TOOLS:
        res = await tools_mod.dispatch_tool_call(tool_name, {"task_id": "pb:001"}, key)
        assert res.get("isError") is True, tool_name
        assert "denied" in res["content"][0]["text"], tool_name

    res = await tools_mod.dispatch_tool_call(
        "tfactory.list_tasks", {"project_id": "pb"}, key
    )
    assert res.get("isError") is True
    assert "denied" in res["content"][0]["text"]
    assert not env.call_internal.called


async def test_org_a_member_succeeds_on_own_org(env):
    key = _key("member-a", "A")
    for tool_name in ALL_TASK_TOOLS:
        res = await tools_mod.dispatch_tool_call(tool_name, {"task_id": "pa:001"}, key)
        assert not res.get("isError"), tool_name

    env.call_internal.reset_mock()
    res = await tools_mod.dispatch_tool_call(
        "tfactory.list_tasks", {"project_id": "pa"}, key
    )
    assert not res.get("isError")
    assert env.call_internal.called


async def test_list_projects_hides_other_org(env):
    res = await tools_mod.dispatch_tool_call(
        "tfactory.list_projects", {}, _key("member-a", "A")
    )
    assert not res.get("isError")
    assert env.call_internal.called
    assert json.loads(res["content"][0]["text"]) == [{"id": "pa"}]


async def test_viewer_key_with_write_scope_denied_writes_and_allowed_reads(env):
    key = _key("viewer-a", "A")
    for tool_name in WRITE_TASK_TOOLS:
        res = await tools_mod.dispatch_tool_call(tool_name, {"task_id": "pa:001"}, key)
        assert res.get("isError") is True, tool_name
        assert "denied" in res["content"][0]["text"], tool_name
    assert not env.call_internal.called

    for tool_name in READ_TASK_TOOLS:
        res = await tools_mod.dispatch_tool_call(tool_name, {"task_id": "pa:001"}, key)
        assert not res.get("isError"), tool_name

    res = await tools_mod.dispatch_tool_call(
        "tfactory.list_tasks", {"project_id": "pa"}, key
    )
    assert not res.get("isError")


async def test_invalid_ids_refused(env):
    key = _key("member-a", "A")
    for bad_task_id in (
        "a:1/../b:2",
        "pa:1/../pb:2",
        "pa",
        ".pa:1",
        "pa:1?x=1",
        "pa:1#x",
        "pa:1%2f",
        123,
        None,
    ):
        res = await tools_mod.dispatch_tool_call(
            "tfactory.get_task", {"task_id": bad_task_id}, key
        )
        assert res.get("isError") is True, bad_task_id
        assert "invalid task_id" in res["content"][0]["text"], bad_task_id

    for bad_project_id in ("pa/../pb", ".pa", 123, None):
        res = await tools_mod.dispatch_tool_call(
            "tfactory.list_tasks", {"project_id": bad_project_id}, key
        )
        assert res.get("isError") is True, bad_project_id
        assert "invalid project_id" in res["content"][0]["text"], bad_project_id

    assert not env.call_internal.called
