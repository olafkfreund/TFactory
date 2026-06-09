"""Tests for the WS3 project migration (JSON → DB, owner's Personal org).

Runs against an in-memory async SQLite DB (StaticPool so the :memory: db
persists across the session). Verifies the happy path, idempotency, both
projects.json shapes, malformed-entry skipping, and the fail-loud guards
(ambiguous owner / no org). The live route is never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
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
    Project,
    User,
)
from server.services.project_migration import (  # noqa: E402
    ProjectMigrationError,
    migrate_projects_to_db,
)


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


async def _seed_user(session, *, email: str, org_name: str = "Personal") -> tuple[str, str]:
    user = User(password_hash="x", email=email, role="user")
    session.add(user)
    await session.flush()
    org = Organization(name=org_name, slug=f"slug-{user.id[:8]}", owner_id=user.id)
    session.add(org)
    await session.flush()
    session.add(OrgMember(org_id=org.id, user_id=user.id, role="owner"))
    await session.commit()
    return user.id, org.id


_MAP_SHAPE = {
    "p1": {"name": "Alpha", "path": "/repos/alpha"},
    "p2": {"path": "/repos/beta"},  # name derived from path
}


async def _projects_in_org(session, org_id: str) -> list[Project]:
    return list(
        (await session.execute(select(Project).where(Project.org_id == org_id)))
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_migrates_into_personal_org(session):
    _, org_id = await _seed_user(session, email="solo@x.com")
    result = await migrate_projects_to_db(session, _MAP_SHAPE)
    assert result.org_id == org_id
    assert result.created_count == 2 and result.skipped_count == 0

    rows = await _projects_in_org(session, org_id)
    by_path = {r.path: r for r in rows}
    assert by_path["/repos/alpha"].name == "Alpha"
    assert by_path["/repos/beta"].name == "beta"  # derived from path basename
    assert all(r.created_by is not None for r in rows)


@pytest.mark.asyncio
async def test_idempotent(session):
    await _seed_user(session, email="solo@x.com")
    first = await migrate_projects_to_db(session, _MAP_SHAPE)
    assert first.created_count == 2
    second = await migrate_projects_to_db(session, _MAP_SHAPE)
    assert second.created_count == 0 and second.skipped_count == 2


@pytest.mark.asyncio
async def test_list_shape_supported(session):
    _, org_id = await _seed_user(session, email="solo@x.com")
    data = {"projects": [{"id": "p1", "name": "Gamma", "path": "/repos/gamma"}]}
    result = await migrate_projects_to_db(session, data)
    assert result.created_count == 1
    assert (await _projects_in_org(session, org_id))[0].name == "Gamma"


@pytest.mark.asyncio
async def test_malformed_entry_skipped(session):
    await _seed_user(session, email="solo@x.com")
    data = {"good": {"path": "/repos/g"}, "bad": {"name": "no path here"}}
    result = await migrate_projects_to_db(session, data)
    assert result.created == ["/repos/g"]


@pytest.mark.asyncio
async def test_ambiguous_owner_fails_loudly(session):
    await _seed_user(session, email="a@x.com")
    await _seed_user(session, email="b@x.com")
    with pytest.raises(ProjectMigrationError, match="2 users"):
        await migrate_projects_to_db(session, _MAP_SHAPE)


@pytest.mark.asyncio
async def test_explicit_owner_disambiguates(session):
    uid_a, org_a = await _seed_user(session, email="a@x.com")
    await _seed_user(session, email="b@x.com")
    result = await migrate_projects_to_db(session, _MAP_SHAPE, owner_user_id=uid_a)
    assert result.org_id == org_a and result.created_count == 2


@pytest.mark.asyncio
async def test_owner_without_org_fails(session):
    # A user with no owned organization.
    user = User(password_hash="x", email="orphan@x.com", role="user")
    session.add(user)
    await session.commit()
    with pytest.raises(ProjectMigrationError, match="owns no organization"):
        await migrate_projects_to_db(session, _MAP_SHAPE, owner_user_id=user.id)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
