"""MCP ``acw_`` key lookup caps scopes by the owner's current org role (#1450)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.database.models import (  # noqa: E402
    ApiKey,
    Base,
    Organization,
    OrgMember,
    User,
)
from server.mcp_remote.auth import MCPAuthError, _lookup_by_digest  # noqa: E402
from sqlalchemy import delete, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """Run ``db(fn)`` with a session on a per-test SQLite DB seeded with one org
    and one key, ``digest-u`` (scopes mcp:read,mcp:write), owned by member ``u``.
    """
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
                    key_hash="acw_xxxx$digest-u",
                    name="k",
                    scopes="mcp:read,mcp:write",
                )
            )
            await s.commit()

    asyncio.run(_init())

    def _run(fn):
        async def _go():
            async with session_local() as s:
                result = await fn(s)
                await s.commit()
                return result

        return asyncio.run(_go())

    yield _run
    asyncio.run(engine.dispose())


def _exec(db, stmt):
    async def _go(s):
        await s.execute(stmt)

    db(_go)


def _scopes(db):
    async def _go(s):
        return (await _lookup_by_digest(s, "digest-u")).scopes

    return db(_go)


def test_member_keeps_stored_scopes(db):
    assert _scopes(db) == {"mcp:read", "mcp:write"}


def _set_role(db, role):
    _exec(db, update(OrgMember).where(OrgMember.user_id == "u").values(role=role))


def test_demoted_to_viewer_keeps_only_read(db):
    _set_role(db, "viewer")
    assert _scopes(db) == {"mcp:read"}


def test_removed_member_key_fails(db):
    _exec(db, delete(OrgMember).where(OrgMember.user_id == "u"))
    with pytest.raises(MCPAuthError, match="no scopes"):
        _scopes(db)


def test_viewer_with_only_write_key_fails(db):
    _set_role(db, "viewer")
    _exec(db, update(ApiKey).values(scopes="mcp:write"))
    with pytest.raises(MCPAuthError, match="no scopes"):
        _scopes(db)


def test_member_of_other_org_only_fails(db):
    async def _move(s):
        s.add(Organization(id="org-2", name="o2", slug="o2", owner_id="u"))
        await s.flush()
        await s.execute(
            update(OrgMember).where(OrgMember.user_id == "u").values(org_id="org-2")
        )

    db(_move)
    with pytest.raises(MCPAuthError, match="no scopes"):
        _scopes(db)
