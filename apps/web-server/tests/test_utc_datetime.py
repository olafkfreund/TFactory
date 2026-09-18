"""Every model timestamp column stores naive UTC, whatever it is handed (#1308).

The columns are ``timestamp without time zone``. Five writers handed them
tz-aware values: SQLite stored those (dropping the offset, so a non-UTC value
landed on the wrong instant) and asyncpg rejected them outright, so on the
deployed Postgres key expiry returned 500 and a test-target credential could
not be resolved. ``UTCDateTime`` normalises at the one place every write passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from server.database.column_types import UTCDateTime
from server.database.models import ApiKey, Base
from sqlalchemy import DateTime
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_PLUS_TWO = timezone(timedelta(hours=2))


def _utc_naive(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> datetime:
    """The naive-UTC value a column stores for this UTC wall time."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC).replace(tzinfo=None)


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (datetime(2026, 9, 25, 12, 0, tzinfo=UTC), _utc_naive(2026, 9, 25, 12)),
        # The same instant, not the same wall time.
        (datetime(2026, 9, 25, 12, 0, tzinfo=_PLUS_TWO), _utc_naive(2026, 9, 25, 10)),
        # Naive is UTC by convention (func.now(), every existing read).
        (_utc_naive(2026, 9, 25, 12), _utc_naive(2026, 9, 25, 12)),
        (None, None),
    ],
)
def test_bind_stores_naive_utc(value: datetime | None, stored: datetime | None) -> None:
    bound = UTCDateTime().process_bind_param(value, sqlite.dialect())
    assert bound == stored
    assert bound is None or bound.tzinfo is None


def test_every_model_timestamp_column_uses_utc_datetime() -> None:
    """A new plain DateTime column would bring the bug back for its writers."""
    plain = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, DateTime)
    ]
    assert plain == [], f"use UTCDateTime, not DateTime: {plain}"
    guarded = [
        col
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, UTCDateTime)
    ]
    assert len(guarded) > 20  # the guard is actually looking at the timestamp columns


@pytest.mark.asyncio
async def test_an_aware_expiry_round_trips_as_the_right_instant() -> None:
    """On SQLite the offset was silently dropped: 12:00+02:00 came back as 12:00."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.tables["api_keys"].create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(
            ApiKey(
                id="k1",
                user_id="u1",
                org_id="o1",
                key_hash="p$1",
                name="a",
                expires_at=datetime(2026, 9, 25, 12, 0, tzinfo=_PLUS_TWO),
            )
        )
        await db.commit()
        db.expunge_all()
        row = await db.get(ApiKey, "k1")
        assert row is not None
        assert row.expires_at == _utc_naive(2026, 9, 25, 10)
    await engine.dispose()
