"""New projects carry the creating principal's org, so enforce mode shows them (#1036)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from fastapi import HTTPException  # noqa: E402

# solo: member of A only; multi: member of A and B.
MEMBERS = [("solo", "A", "member"), ("multi", "A", "member"), ("multi", "B", "member")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from server.database.models import Base, Organization, OrgMember, User
    from server.routes import project_authz as pa
    from server.routes import projects
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("solo", "multi", "owner"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            for oid in ("A", "B"):
                db.add(Organization(id=oid, name=oid, slug=oid, owner_id="owner"))
            await db.flush()
            for uid, oid, role in MEMBERS:
                db.add(OrgMember(org_id=oid, user_id=uid, role=role))
            await db.commit()

    asyncio.run(_init())
    settings = SimpleNamespace(
        DISABLE_AUTH=False,
        RBAC_ENFORCE_TASK_ROUTES=True,
        PROJECTS_BACKEND="json",
        LEGACY_SHARED_TOKEN_FULL_ACCESS=True,
    )
    monkeypatch.setattr(pa, "get_settings", lambda: settings)
    store: dict = {}
    monkeypatch.setattr(pa, "_load_projects", lambda: store)
    monkeypatch.setattr(projects, "load_projects", lambda: store)
    monkeypatch.setattr(projects, "save_projects", lambda data: None)

    def run(coro_fn):
        async def _go():
            async with session_local() as db:
                return await coro_fn(db)

        return asyncio.run(_go())

    return SimpleNamespace(run=run, pa=pa, store=store)


def test_creator_org_by_principal(env):
    cases = [
        ({"id": "solo"}, "A"),  # one membership
        ({"id": "multi"}, "A"),  # deterministic first org
        (
            {
                "id": "x",
                "api_key_id": "k",
                "org_id": "B",
                "scopes": ["project:write"],
                "scoped_service": True,
            },
            "B",
        ),  # org service key
        ({"id": "default", "is_legacy_token": True}, None),  # legacy service principal
    ]
    for principal, expected in cases:
        assert env.run(lambda db, p=principal: env.pa.creator_org(p, db)) == expected


def test_add_project_stamps_creator_org_and_is_visible_under_enforce(env, tmp_path):
    from server.routes import projects

    req = SimpleNamespace(state=SimpleNamespace(user={"id": "solo"}))
    body = projects.ProjectCreate(path=str(tmp_path / "proj"))
    out = env.run(lambda db: projects.add_project(body, req, db))
    assert env.store[out["id"]]["org_id"] == "A"
    env.run(
        lambda db: env.pa.authorize_project_for_user({"id": "solo"}, out["id"], db)
    )  # no 403


def test_unstamped_project_is_404_for_non_service(env, tmp_path):
    from server.routes import projects

    body = projects.ProjectCreate(path=str(tmp_path / "orphan"))
    out = asyncio.run(projects.create_project(body))  # service caller: no org
    assert "org_id" not in env.store[out["id"]]
    with pytest.raises(HTTPException) as exc:
        env.run(
            lambda db: env.pa.authorize_project_for_user({"id": "solo"}, out["id"], db)
        )
    assert exc.value.status_code == 404


def test_clone_register_stamps_org(env, tmp_path, monkeypatch):
    from server.routes import specs

    async def fake_clone(**_kw):
        return tmp_path

    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", fake_clone
    )
    pdata, pid = asyncio.run(
        specs._clone_and_register_project(
            "https://x/y.git", branch=None, name="y", org_id="A"
        )
    )
    assert pdata["org_id"] == "A" and env.store[pid]["org_id"] == "A"
