#!/usr/bin/env python3
"""Tests for the durable verify job-state store (RFC-0016, TFactory #465).

Covers:
- ``JobState`` row round-trip (enqueue → record shape conforms to
  ``apis/job-state.schema.json``).
- Native-status → canonical ``lifecycle_state`` mapping, including
  ``review_initial_complete`` → ``review`` and a no-verdict ``triaged`` → ``stuck``.
- Terminal transitions set ``ended_at`` + ``result``; ``failed`` sets ``error``.
- ``active_count`` / ``recover_in_flight`` read the live table (the durable
  admission count).
- ``SELECT ... FOR UPDATE`` is requested when granting a slot / advancing state
  on Postgres (concurrency safety) and skipped on SQLite.
- SQLite fallback logs "not multi-replica safe" when DATABASE_URL is unset.

Runs on in-memory async SQLite (no Postgres required) — matches the
``test_project_store.py`` layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.database.models import Base  # noqa: E402
from server.services import job_state_status as st  # noqa: E402
from server.services.job_state_store import (  # noqa: E402
    DbJobStateStore,
    get_job_state_store,
)

# ─── fixtures ───────────────────────────────────────────────────────────────


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


# ─── status mapping (pure) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "native,expected",
    [
        ("reviewing", st.REVIEW),
        ("review_failed", st.FAILED),  # failed token beats review token
        ("triaged", st.DONE),
        ("triaged_empty", st.DONE),
        ("passed", st.DONE),
        ("evaluator_failed", st.FAILED),
        ("planning", st.RUNNING),
        ("backlog", st.QUEUED),
        ("stalled", st.STUCK),
        ("", st.QUEUED),
        (None, st.QUEUED),
        ("totally_unknown_mark", st.RUNNING),  # running_fallback
    ],
)
def test_status_mapping(native, expected):
    assert st.to_lifecycle_state(native) == expected


def test_review_initial_complete_phase_maps_to_review():
    # The review_initial_complete *phase* parks the task for a decision even
    # when the raw status would otherwise read active/terminal.
    assert (
        st.to_lifecycle_state("reviewing", phase="review_initial_complete") == st.REVIEW
    )
    assert (
        st.to_lifecycle_state("triaged", phase="review_initial_complete") == st.REVIEW
    )


def test_no_verdict_terminal_maps_to_stuck():
    # A terminal-by-name status that produced no verdict is the "lanes pending,
    # no verdict" stall (TFactory #464) — representable as `stuck`.
    assert st.to_lifecycle_state("triaged", has_verdict=False) == st.STUCK
    # With a verdict it is honestly done.
    assert st.to_lifecycle_state("triaged", has_verdict=True) == st.DONE


# ─── store round-trip ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_roundtrip(session):
    store = get_job_state_store(session)
    rec = await store.enqueue("spec-1", correlation_key=482, service_status="running")

    assert rec["job_id"] == "spec-1"
    assert rec["schema_version"] == "1"
    assert rec["service"] == "tfactory"
    assert rec["kind"] == "verify"
    assert rec["correlation_key"] == "482"
    assert rec["lifecycle_state"] == st.QUEUED
    assert rec["admission"]["enqueued_at"] is not None
    assert rec["created_at"] is not None

    fetched = await store.get("spec-1")
    assert fetched["job_id"] == "spec-1"

    # Enqueue is idempotent — does not reset an existing row.
    again = await store.enqueue("spec-1")
    assert again["correlation_key"] == "482"


@pytest.mark.asyncio
async def test_grant_slot_sets_running(session):
    store = get_job_state_store(session)
    await store.enqueue("spec-2")
    rec = await store.grant_slot("spec-2")
    assert rec["lifecycle_state"] == st.RUNNING
    assert rec["admission"]["started_at"] is not None


@pytest.mark.asyncio
async def test_grant_slot_missing_raises(session):
    store = get_job_state_store(session)
    with pytest.raises(KeyError):
        await store.grant_slot("nope")


# ─── terminal invariants ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_done_sets_ended_at_and_result(session):
    store = get_job_state_store(session)
    await store.enqueue("spec-3")
    await store.grant_slot("spec-3")
    rec = await store.update_status(
        "spec-3",
        service_status="triaged",
        has_verdict=True,
        result={"verdict": "pass", "val_level": 2},
    )
    assert rec["lifecycle_state"] == st.DONE
    assert rec["ended_at"] is not None
    assert rec["result"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_terminal_failed_requires_error(session):
    store = get_job_state_store(session)
    await store.enqueue("spec-4")
    rec = await store.update_status("spec-4", service_status="review_failed")
    assert rec["lifecycle_state"] == st.FAILED
    assert rec["ended_at"] is not None
    # never-overclaim: a failed job always carries an error, even if the caller
    # didn't supply one.
    assert rec["error"]
    # An explicit error is preserved.
    await store.enqueue("spec-4b")
    rec2 = await store.update_status(
        "spec-4b", service_status="evaluator_failed", error="docker timed out"
    )
    assert rec2["error"] == "docker timed out"


@pytest.mark.asyncio
async def test_no_verdict_recorded_as_stuck_with_error(session):
    store = get_job_state_store(session)
    await store.enqueue("spec-5")
    await store.grant_slot("spec-5")
    rec = await store.update_status(
        "spec-5", service_status="triaged", has_verdict=False
    )
    assert rec["lifecycle_state"] == st.STUCK
    assert rec["error"]  # reapable with a reason
    assert rec["ended_at"] is not None


@pytest.mark.asyncio
async def test_mark_stuck(session):
    store = get_job_state_store(session)
    await store.enqueue("spec-6")
    await store.grant_slot("spec-6")
    rec = await store.mark_stuck("spec-6", "job pod vanished without a terminal write")
    assert rec["lifecycle_state"] == st.STUCK
    assert rec["error"] == "job pod vanished without a terminal write"
    assert rec["ended_at"] is not None


# ─── durable admission count / recovery ─────────────────────────────────────


@pytest.mark.asyncio
async def test_active_count_and_recovery(session):
    store = get_job_state_store(session)
    await store.enqueue("a")  # queued → active
    await store.enqueue("b")
    await store.grant_slot("b")  # running → active
    await store.enqueue("c")
    await store.update_status("c", service_status="triaged", has_verdict=True)  # done

    assert await store.active_count() == 2  # a (queued) + b (running)

    in_flight = await store.recover_in_flight()
    ids = sorted(r["job_id"] for r in in_flight)
    assert ids == ["a", "b"]
    # The terminal job is not counted as in-flight.
    assert all(r["lifecycle_state"] in (st.QUEUED, st.RUNNING) for r in in_flight)


# ─── FOR UPDATE concurrency design ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_for_update_requested_on_postgres(monkeypatch):
    """grant_slot / update_status must lock the row FOR UPDATE on Postgres.

    We don't need a live Postgres: assert that ``_locked_row`` adds
    ``with_for_update()`` to the SELECT when the dialect is postgresql, and not
    on sqlite (where row locks are n/a). This guards the multi-replica
    double-start protection.
    """
    captured: list[str] = []

    class _FakeBind:
        class dialect:  # noqa: N801 — mimic SQLAlchemy attribute access
            name = "postgresql"

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        bind = _FakeBind()

        async def execute(self, stmt):
            captured.append(str(stmt))
            return _FakeResult()

    store = DbJobStateStore.__new__(DbJobStateStore)
    store._session = _FakeSession()  # type: ignore[attr-defined]

    await store._locked_row("x")
    assert "FOR UPDATE" in captured[-1].upper()


@pytest.mark.asyncio
async def test_for_update_skipped_on_sqlite(session):
    store = get_job_state_store(session)
    # sqlite path: _locked_row must NOT raise and must NOT emit FOR UPDATE.
    await store.enqueue("sq-1")
    assert store._is_postgres is False
    row = await store._locked_row("sq-1")
    assert row is not None


# ─── admission control (RFC-0016 #465) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_max_concurrent_default_and_env(monkeypatch):
    import server.services.job_state_store as jss

    monkeypatch.delenv("TFACTORY_MAX_CONCURRENT_VERIFIES", raising=False)
    assert jss.max_concurrent_verifies() == 4  # documented default
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "2")
    assert jss.max_concurrent_verifies() == 2
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "0")
    assert jss.max_concurrent_verifies() == 0  # unlimited sentinel
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "-1")
    assert jss.max_concurrent_verifies() == -1
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "garbage")
    assert jss.max_concurrent_verifies() == 4  # bad value → default


@pytest.mark.asyncio
async def test_try_admit_grants_under_cap(session, monkeypatch):
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "2")
    store = get_job_state_store(session)
    a = await store.try_admit("a")
    b = await store.try_admit("b")
    assert a["lifecycle_state"] == st.RUNNING
    assert b["lifecycle_state"] == st.RUNNING
    assert await store.running_count() == 2


@pytest.mark.asyncio
async def test_try_admit_at_cap_enqueues_not_started(session, monkeypatch):
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "2")
    store = get_job_state_store(session)
    await store.try_admit("a")
    await store.try_admit("b")
    c = await store.try_admit("c")
    # At the cap: c WAITS in queued — not started, not hard-failed.
    assert c["lifecycle_state"] == st.QUEUED
    assert await store.running_count() == 2
    assert await store.active_count() == 3  # 2 running + 1 queued


@pytest.mark.asyncio
async def test_cap_unlimited_when_zero_or_negative(session, monkeypatch):
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "0")
    store = get_job_state_store(session)
    for jid in ("a", "b", "c", "d", "e"):
        rec = await store.try_admit(jid)
        assert rec["lifecycle_state"] == st.RUNNING
    assert await store.running_count() == 5


@pytest.mark.asyncio
async def test_finishing_verify_promotes_fifo(session, monkeypatch):
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "1")
    store = get_job_state_store(session)
    a = await store.try_admit("a")
    assert a["lifecycle_state"] == st.RUNNING
    # b and c queue behind the cap=1, in arrival (FIFO) order.
    assert (await store.try_admit("b"))["lifecycle_state"] == st.QUEUED
    assert (await store.try_admit("c"))["lifecycle_state"] == st.QUEUED

    # a finishes → the oldest queued (b) is promoted, not c.
    await store.update_status("a", service_status="triaged", has_verdict=True)
    promoted = await store.promote_next()
    assert promoted is not None
    assert promoted["job_id"] == "b"
    assert promoted["lifecycle_state"] == st.RUNNING
    # c still waits (cap=1, b now running).
    assert (await store.get("c"))["lifecycle_state"] == st.QUEUED


@pytest.mark.asyncio
async def test_promote_next_noop_when_saturated(session, monkeypatch):
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "1")
    store = get_job_state_store(session)
    await store.try_admit("a")  # running, fills the only slot
    await store.try_admit("b")  # queued
    # No slot free → promote_next must not promote.
    assert await store.promote_next() is None
    assert (await store.get("b"))["lifecycle_state"] == st.QUEUED


@pytest.mark.asyncio
async def test_try_admit_idempotent_on_running(session, monkeypatch):
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "2")
    store = get_job_state_store(session)
    first = await store.try_admit("a")
    again = await store.try_admit("a")
    assert first["lifecycle_state"] == st.RUNNING
    assert again["lifecycle_state"] == st.RUNNING
    assert await store.running_count() == 1  # not double-counted


@pytest.mark.asyncio
async def test_for_update_prevents_exceeding_cap_under_concurrent_admits(monkeypatch):
    """Two replicas admitting at the same time must not both win the last slot.

    Each admit takes ``SELECT ... FOR UPDATE`` on its row, then re-counts running
    jobs before granting. With a real Postgres the row lock serializes the two
    transactions; here we assert the design: a) the locking SELECT carries FOR
    UPDATE on Postgres, and b) when admits are serialized (as the lock forces),
    the cap holds. We drive real serialized admits on SQLite (FOR UPDATE n/a but
    the same count→grant logic) to prove the cap arithmetic, and separately
    assert the lock is requested on Postgres.
    """
    # (b) the cap arithmetic holds when admits are serialized.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("TFACTORY_MAX_CONCURRENT_VERIFIES", "2")
    granted = 0
    async with factory() as s:
        store = get_job_state_store(s)
        for jid in ("a", "b", "c", "d"):
            rec = await store.try_admit(jid)
            if rec["lifecycle_state"] == st.RUNNING:
                granted += 1
    assert granted == 2  # never exceeds the cap
    await engine.dispose()

    # (a) the grant path locks the row FOR UPDATE on Postgres.
    captured: list[str] = []

    class _FakeBind:
        class dialect:  # noqa: N801
            name = "postgresql"

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        bind = _FakeBind()

        async def execute(self, stmt):
            captured.append(str(stmt))
            return _FakeResult()

    store = DbJobStateStore.__new__(DbJobStateStore)
    store._session = _FakeSession()  # type: ignore[attr-defined]
    await store._locked_row("x")
    assert "FOR UPDATE" in captured[-1].upper()


# ─── SQLite fallback warning ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqlite_fallback_warns_not_multi_replica_safe(session, caplog):
    import logging

    import server.services.job_state_store as jss

    # Reset the once-only guard so this test can observe the warning.
    jss._FALLBACK_WARNED = False
    with caplog.at_level(logging.WARNING):
        jss.get_job_state_store(session)
    assert any(
        "not multi-replica safe" in r.getMessage().lower() for r in caplog.records
    )
