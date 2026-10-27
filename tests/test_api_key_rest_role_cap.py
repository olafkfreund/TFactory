"""REST ``acw_`` key auth caps scopes by the owner's current org role (#1450)."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.database.models import (  # noqa: E402
    ApiKey,
    Base,
    Organization,
    OrgMember,
    User,
)
from sqlalchemy import delete, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

RAW = "acw_restcaptoken"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as s:
            s.add(User(id="u", email="u@x.test", password_hash=""))
            await s.flush()
            s.add(Organization(id="org-1", name="o", slug="o", owner_id="u"))
            await s.flush()
            s.add(OrgMember(org_id="org-1", user_id="u", role="member"))
            s.add(
                ApiKey(
                    user_id="u",
                    org_id="org-1",
                    key_hash="acw_rest$" + hashlib.sha256(RAW.encode()).hexdigest(),
                    name="k",
                    scopes="api:full",
                )
            )
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_local() as s:
            yield s

    # Resolved at call time: other suite tests swap server.* modules in
    # sys.modules, and a module-level reference would patch a stale copy.
    import server.mcp_remote.auth as mcp_auth  # noqa: PLC0415
    from server import auth as auth_mod  # noqa: PLC0415
    from server.config import get_settings  # noqa: PLC0415

    monkeypatch.setattr(mcp_auth, "get_db", _get_db)
    monkeypatch.setattr(get_settings(), "DISABLE_AUTH", False, raising=False)

    app = FastAPI()
    app.add_middleware(auth_mod.TokenAuthMiddleware)

    @app.get("/api/probe")
    async def probe():
        return {"ok": True}

    def _exec(stmt):
        async def _go():
            async with session_local() as s:
                await s.execute(stmt)
                await s.commit()

        asyncio.run(_go())

    yield TestClient(app), _exec
    asyncio.run(engine.dispose())


def _status(client):
    return client.get(
        "/api/probe", headers={"Authorization": f"Bearer {RAW}"}
    ).status_code


def test_member_key_with_api_scope_authenticates(env):
    assert _status(env[0]) == 200


def test_removed_member_key_is_rejected(env):
    client, run = env
    run(delete(OrgMember).where(OrgMember.user_id == "u"))
    assert _status(client) == 403


def test_demoted_to_viewer_key_is_rejected(env):
    client, run = env
    run(update(OrgMember).where(OrgMember.user_id == "u").values(role="viewer"))
    assert _status(client) == 403
