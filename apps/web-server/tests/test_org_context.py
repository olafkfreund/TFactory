"""Tests for the WS3 org resolver (slice 1c foundation).

In-memory async SQLite. Covers the resolution precedence (owned Personal →
owned other → membership → None) and the request-state wrapper.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
for _p in (_WEB_SERVER, _BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from server.database.models import (  # noqa: E402
    Base,
    OrgMember,
    Organization,
    User,
)
from server.services.org_context import resolve_request_org, resolve_user_org  # noqa: E402


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _user(session, email: str) -> str:
    u = User(password_hash="x", email=email, role="user")
    session.add(u)
    await session.flush()
    await session.commit()
    return u.id


async def _org(session, *, owner_id: str, name: str) -> str:
    o = Organization(name=name, slug=f"s-{name}-{owner_id[:6]}", owner_id=owner_id)
    session.add(o)
    await session.flush()
    await session.commit()
    return o.id


@pytest.mark.asyncio
async def test_prefers_owned_personal_org(session):
    uid = await _user(session, "a@x.com")
    await _org(session, owner_id=uid, name="Team")
    personal = await _org(session, owner_id=uid, name="Personal")
    assert await resolve_user_org(session, uid) == personal


@pytest.mark.asyncio
async def test_falls_back_to_any_owned_org(session):
    uid = await _user(session, "a@x.com")
    team = await _org(session, owner_id=uid, name="Team")
    assert await resolve_user_org(session, uid) == team


@pytest.mark.asyncio
async def test_membership_when_not_owner(session):
    owner = await _user(session, "owner@x.com")
    member = await _user(session, "member@x.com")
    org = await _org(session, owner_id=owner, name="Personal")
    session.add(OrgMember(org_id=org, user_id=member, role="member"))
    await session.commit()
    assert await resolve_user_org(session, member) == org


@pytest.mark.asyncio
async def test_unaffiliated_user_returns_none(session):
    uid = await _user(session, "lonely@x.com")
    assert await resolve_user_org(session, uid) is None


@pytest.mark.asyncio
async def test_resolve_request_org_reads_state(session):
    uid = await _user(session, "a@x.com")
    org = await _org(session, owner_id=uid, name="Personal")
    request = SimpleNamespace(state=SimpleNamespace(user={"id": uid}))
    assert await resolve_request_org(request, session) == org


@pytest.mark.asyncio
async def test_resolve_request_org_no_user(session):
    request = SimpleNamespace(state=SimpleNamespace(user=None))
    assert await resolve_request_org(request, session) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
