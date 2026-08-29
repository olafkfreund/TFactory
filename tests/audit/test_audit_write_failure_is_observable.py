"""#1252 follow-up — a dropped audit entry must not be silent.

Both write paths in ``audit_service`` catch their exception so audit logging
can never crash the calling operation. That is right for the caller and wrong
for the reader: a dropped entry is indistinguishable from an action that never
happened. The hash chain cannot close the gap — ``prev_hash``/``entry_hash``
detect a MUTATED row, and a row that was never written leaves the surviving
chain perfectly intact. That is what made TFactory#1252 invisible for three
production rows.

So the drop stays non-fatal and stops being silent. Two assertions, because
each covers a different consumer: ERROR is what a human grep/alert-on-level
sees, and the counter is what a dashboard can act on before anyone greps.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[2] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.services import audit_service  # noqa: E402


class _ExplodingSession:
    """A session whose flush fails the way a too-narrow column does."""

    def __init__(self) -> None:
        self.added: list[object] = []

    async def execute(self, *_a, **_k):  # pragma: no cover - not reached
        raise AssertionError("execute should not be reached")

    def add(self, entry: object) -> None:
        self.added.append(entry)

    async def flush(self) -> None:
        raise RuntimeError("value too long for type character varying(36)")


def _counter_value(action: str, resource_type: str) -> float:
    counter = audit_service.AUDIT_WRITE_FAILURES
    # Deliberately NOT pytest.skip: a skip renders as "not failed", so a run
    # where the counter had silently become None would look exactly like a
    # passing one. That is the defect this module is about, and a test is not
    # exempt from it.
    assert counter is not None, (
        "AUDIT_WRITE_FAILURES is None — the counter was never registered, so "
        "dropped audit entries are uncountable again"
    )
    return counter.labels(action=action, resource_type=resource_type)._value.get()


@pytest.mark.audit
@pytest.mark.asyncio
async def test_failed_audit_write_logs_error_and_counts(caplog) -> None:
    action, resource_type = "test.dropped", "task"
    before = _counter_value(action, resource_type)

    with caplog.at_level(logging.ERROR, logger=audit_service.__name__):
        await audit_service.log_audit_event(
            db=_ExplodingSession(),  # type: ignore[arg-type]
            action=action,
            resource_type=resource_type,
            resource_id="a-composite:id-that-would-not-fit",
        )

    # Still non-fatal — the caller's operation is not crashed by the drop.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, (
        "a dropped audit entry logged below ERROR pages nobody; TFactory#1252 "
        "sat at WARNING while an entire class of action went unrecorded"
    )
    assert "Failed to write audit log entry" in errors[0].getMessage()

    after = _counter_value(action, resource_type)
    assert after == before + 1, (
        "the drop must be countable — a log line is evidence after you already "
        "suspect something, a counter is how you come to suspect it"
    )


@pytest.mark.audit
@pytest.mark.asyncio
async def test_a_successful_write_counts_nothing(caplog) -> None:
    """The false-alarm direction: a healthy write must not touch the counter."""

    class _OkSession(_ExplodingSession):
        async def execute(self, *_a, **_k):
            class _R:
                @staticmethod
                def scalar_one_or_none():
                    return None

            return _R()

        async def flush(self) -> None:
            return None

    action, resource_type = "test.ok", "task"
    before = _counter_value(action, resource_type)
    with caplog.at_level(logging.ERROR, logger=audit_service.__name__):
        await audit_service.log_audit_event(
            db=_OkSession(),  # type: ignore[arg-type]
            action=action,
            resource_type=resource_type,
            resource_id="fine",
        )
    assert _counter_value(action, resource_type) == before, (
        "a successful audit write must not increment the failure counter"
    )
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
