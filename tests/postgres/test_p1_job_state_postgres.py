"""Durable verify job-state store against REAL Postgres (RFC-0016, TFactory #465).

The unit tests in ``tests/test_job_state_store.py`` run on in-memory SQLite,
which is permissive about types (it stores datetimes as text). Postgres + asyncpg
is strict: a ``TIMESTAMP WITHOUT TIME ZONE`` column rejects a tz-aware datetime
("can't subtract offset-naive and offset-aware datetimes"). These tests exercise
the store against a live Postgres so that class of bug — plus the
Postgres-only ``SELECT ... FOR UPDATE`` row-lock path — is covered in CI's
``postgres`` lane, not just SQLite.

Marked ``postgres`` + ``slow`` so the default ``-m "not slow"`` gate skips them;
the ``postgres-acceptance`` CI job opts in with ``-m postgres`` and provides a
live PG 15/16 via service container (``TEST_POSTGRES_URL``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_WEB_SERVER = Path(__file__).resolve().parents[2] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.database.models import Base  # noqa: E402
from server.services import job_state_status as st  # noqa: E402
from server.services.job_state_store import get_job_state_store  # noqa: E402

pytestmark = [pytest.mark.postgres, pytest.mark.slow]


@pytest_asyncio.fixture
async def pg_session(test_postgres_url: str):
    """Async session bound to the live test Postgres, on an isolated schema.

    Creates the ORM tables in a throwaway schema so the round-trip doesn't
    depend on (or collide with) whatever migration state the shared CI database
    is in, and drops it on teardown.
    """
    from sqlalchemy import text

    engine = create_async_engine(test_postgres_url, future=True)
    schema = "job_state_rt_test"
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text(f'SET search_path TO "{schema}"'))
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Base.metadata.tables["job_states"]]
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    # Pin the session's connection to the test schema for every statement.
    await session.execute(text(f'SET search_path TO "{schema}"'))
    try:
        yield session
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_full_lifecycle_roundtrip_on_postgres(pg_session):
    """enqueue → grant (FOR UPDATE) → terminal done, with naive-datetime writes.

    This is the regression guard for the tz-aware-vs-naive ``ended_at`` bug:
    asyncpg raises a DataError on a tz-aware value for a naive column, so a
    green run proves the column writes are naive UTC.
    """
    store = get_job_state_store(pg_session)
    assert store._is_postgres is True

    await store.enqueue("pg-1", correlation_key=482, service_status="running")
    granted = await store.grant_slot("pg-1")  # exercises SELECT ... FOR UPDATE
    assert granted["lifecycle_state"] == st.RUNNING
    assert granted["admission"]["started_at"] is not None

    done = await store.update_status(
        "pg-1", service_status="triaged", has_verdict=True, result={"verdict": "pass"}
    )
    assert done["lifecycle_state"] == st.DONE
    assert done["ended_at"] is not None  # naive UTC write succeeded
    assert done["result"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_failed_and_stuck_set_error_and_ended_at_on_postgres(pg_session):
    store = get_job_state_store(pg_session)

    await store.enqueue("pg-fail")
    failed = await store.update_status("pg-fail", service_status="review_failed")
    assert failed["lifecycle_state"] == st.FAILED
    assert failed["error"]
    assert failed["ended_at"] is not None

    await store.enqueue("pg-stuck")
    stuck = await store.update_status(
        "pg-stuck", service_status="triaged", has_verdict=False
    )
    assert stuck["lifecycle_state"] == st.STUCK
    assert stuck["error"]
    assert stuck["ended_at"] is not None

    reaped = await store.mark_stuck("pg-stuck", "pod vanished")
    assert reaped["lifecycle_state"] == st.STUCK
    assert reaped["ended_at"] is not None


@pytest.mark.asyncio
async def test_active_count_and_recovery_on_postgres(pg_session):
    store = get_job_state_store(pg_session)
    await store.enqueue("a")
    await store.enqueue("b")
    await store.grant_slot("b")
    await store.enqueue("c")
    await store.update_status("c", service_status="triaged", has_verdict=True)

    assert await store.active_count() == 2
    ids = sorted(r["job_id"] for r in await store.recover_in_flight())
    assert ids == ["a", "b"]
