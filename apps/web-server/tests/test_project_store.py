"""Tests for the WS3 ProjectStore abstraction (slice 1b).

JsonProjectStore round-trip on a tmp file; DbProjectStore org-scoped CRUD +
reconcile (upsert/delete) on in-memory async SQLite; and the get_project_store
factory honouring APP_PROJECTS_BACKEND. No route is touched.
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

from server.database.models import Base, Organization, User  # noqa: E402
from server.services import project_store as ps  # noqa: E402
from server.services.project_store import (  # noqa: E402
    DbProjectStore,
    JsonProjectStore,
    get_project_store,
)


# ─── JsonProjectStore ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_json_store_roundtrip(tmp_path):
    store = JsonProjectStore(tmp_path / "projects.json")
    assert await store.load_all() == {}  # missing file → empty
    data = {"p1": {"name": "Alpha", "path": "/a"}, "p2": {"name": "Beta", "path": "/b"}}
    await store.save_all(data)
    assert await store.load_all() == data


# ─── DbProjectStore ────────────────────────────────────────────────────────


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


async def _org(session, *, email: str) -> str:
    user = User(password_hash="x", email=email, role="user")
    session.add(user)
    await session.flush()
    org = Organization(name="Personal", slug=f"s-{user.id[:8]}", owner_id=user.id)
    session.add(org)
    await session.flush()
    await session.commit()
    return org.id


@pytest.mark.asyncio
async def test_db_store_insert_and_load(session):
    org_id = await _org(session, email="a@x.com")
    store = DbProjectStore(session, org_id)
    await store.save_all({"p1": {"name": "Alpha", "path": "/a", "extra": 7}})
    loaded = await store.load_all()
    assert loaded["p1"]["name"] == "Alpha"
    assert loaded["p1"]["path"] == "/a"
    assert loaded["p1"]["extra"] == 7  # round-trips via settings_json


@pytest.mark.asyncio
async def test_db_store_reconcile_updates_and_deletes(session):
    org_id = await _org(session, email="a@x.com")
    store = DbProjectStore(session, org_id)
    await store.save_all({"p1": {"name": "A", "path": "/a"}, "p2": {"name": "B", "path": "/b"}})
    # drop p2, rename p1
    await store.save_all({"p1": {"name": "A2", "path": "/a"}})
    loaded = await store.load_all()
    assert set(loaded) == {"p1"} and loaded["p1"]["name"] == "A2"


@pytest.mark.asyncio
async def test_db_store_is_org_scoped(session):
    org_a = await _org(session, email="a@x.com")
    org_b = await _org(session, email="b@x.com")
    await DbProjectStore(session, org_a).save_all({"pa": {"name": "A", "path": "/a"}})
    await DbProjectStore(session, org_b).save_all({"pb": {"name": "B", "path": "/b"}})
    assert set(await DbProjectStore(session, org_a).load_all()) == {"pa"}
    assert set(await DbProjectStore(session, org_b).load_all()) == {"pb"}


# ─── factory ───────────────────────────────────────────────────────────────


def test_factory_defaults_to_json(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ps, "get_settings",
        lambda: SimpleNamespace(PROJECTS_BACKEND="json", PROJECTS_DATA_DIR=str(tmp_path)),
    )
    store = get_project_store()
    assert isinstance(store, JsonProjectStore)


def test_factory_db_requires_session_and_org(monkeypatch):
    monkeypatch.setattr(
        ps, "get_settings", lambda: SimpleNamespace(PROJECTS_BACKEND="db", PROJECTS_DATA_DIR="")
    )
    with pytest.raises(ValueError, match="session \\+ org_id"):
        get_project_store()


@pytest.mark.asyncio
async def test_factory_db_returns_db_store(monkeypatch, session):
    monkeypatch.setattr(
        ps, "get_settings", lambda: SimpleNamespace(PROJECTS_BACKEND="db", PROJECTS_DATA_DIR="")
    )
    org_id = await _org(session, email="a@x.com")
    store = get_project_store(session=session, org_id=org_id)
    assert isinstance(store, DbProjectStore)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
