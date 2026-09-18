"""Column types shared by the ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """A naive-UTC timestamp column that accepts tz-aware values (#1308).

    Every timestamp column is ``timestamp without time zone`` holding UTC wall
    time (matching ``func.now()``). Writers commonly hand it
    ``datetime.now(UTC)``: asyncpg rejects an aware value for a naive column
    ("can't subtract offset-naive and offset-aware datetimes") and SQLite
    silently drops the offset, so a non-UTC value lands on the wrong instant.
    Normalising here, the one place every write passes, fixes every writer and
    keeps the next one from reintroducing it. Reads are unchanged (naive UTC).
    """

    impl = DateTime  # naive: the stored column type does not change
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,  # noqa: ARG002 - SQLAlchemy's TypeDecorator signature
    ) -> Any:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
