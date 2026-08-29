"""Issue #1252 — audit_logs.resource_id must hold a composite task id.

``resource_id`` was ``String(36)`` (a UUID's length) while the task pipeline
builds ``"{project_id}:{spec_slug}"`` -- 53+ characters. On Postgres that is a
``StringDataRightTruncationError``, which ``audit_service`` catches and logs at
WARNING. The API returned success and NO ROW WAS WRITTEN.

So the assertion here is deliberately "the row exists", not "no exception
propagated": the exception is already swallowed on unfixed code, which is the
entire defect. SQLite cannot host this test either -- it does not enforce
VARCHAR length, so the truncation never happens there. Postgres is the only
place the bug is observable end to end. The hermetic schema-width companion
lives in ``tests/audit/test_audit_resource_id_width.py``.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import pytest

from tests.postgres.helpers import WEB_SERVER_ROOT, alembic_available, run_alembic

# The exact shape observed in the failing PARR run:
# "502baac8-4816-42bb-bf2d-c54a38087302:pending-bf5ee0dc" -- 53 chars.
COMPOSITE_RESOURCE_ID = f"{uuid.uuid4()}:pending-{uuid.uuid4().hex[:8]}"


@pytest.mark.postgres
@pytest.mark.slow
def test_composite_task_resource_id_is_persisted(test_postgres_url: str) -> None:
    """A composite ``<uuid>:<slug>`` resource_id must produce a real audit row."""
    if not alembic_available():
        pytest.skip("alembic not importable")

    upgraded = run_alembic(["upgrade", "head"], env={"DATABASE_URL": test_postgres_url})
    assert upgraded.returncode == 0, (
        f"alembic upgrade head failed:\n{upgraded.stderr[-2000:]}"
    )

    assert len(COMPOSITE_RESOURCE_ID) > 36, "test id must exceed the old width"

    if str(WEB_SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(WEB_SERVER_ROOT))

    async def _write_and_read() -> int:
        from server.services.audit_service import (
            ACTION_MCP_TASK_CREATE_AND_RUN,
            log_audit_event,
        )
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(test_postgres_url)
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await log_audit_event(
                    db=session,
                    action=ACTION_MCP_TASK_CREATE_AND_RUN,
                    resource_type="task",
                    resource_id=COMPOSITE_RESOURCE_ID,
                )
                # Swallow like the production callers do: they treat audit
                # logging as best-effort and return success regardless. This
                # test must fail on the MISSING ROW, not on an exception the
                # real system never sees.
                try:
                    await session.commit()
                except Exception:  # pragma: no cover - only on unfixed schema
                    await session.rollback()

            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT count(*) FROM audit_logs WHERE resource_id = :rid"),
                    {"rid": COMPOSITE_RESOURCE_ID},
                )
                return int(result.scalar_one())
        finally:
            await engine.dispose()

    rows = asyncio.run(_write_and_read())
    assert rows == 1, (
        f"no audit row for resource_id={COMPOSITE_RESOURCE_ID!r}. The write was "
        "swallowed by audit_service's except/warning path -- the audit chain has "
        "a hole where an audited task action should be."
    )
