"""A dead credential must not be reported as "the agent produced nothing" — #854.

`safe_receive_messages` exists to keep a session alive through a transient
mid-stream glitch. It caught *every* exception, including the SDK's
authentication failure, logged it, and returned cleanly — so the caller saw a
normal end-of-stream with no output.

Downstream that became "the planner emitted no plan", which the planner then
retried in full, at cost, against a credential that could not possibly work:

    Failed to authenticate: OAuth session expired and could not be refreshed
    WARNI [agents.planner.tfactory] planner: first session produced missing
    (None); retrying once

    {"status": "planner_failed",
     "phase": "planner_invalid_missing_after_retry",
     "planner_error": "after retry: missing — "}

Two defects are covered here:
  - the classifier did not recognise the real message, so the failure was not
    even labelled `authentication`
  - the stream wrapper swallowed it regardless
"""

from __future__ import annotations

from typing import Any

import pytest
from core.error_utils import is_authentication_error, safe_receive_messages


class _Client:
    """Yields ``before``, then raises ``exc`` mid-stream."""

    def __init__(self, exc: Exception, before: tuple[Any, ...] = ()) -> None:
        self._exc = exc
        self._before = before

    async def receive_response(self):
        for msg in self._before:
            yield msg
        raise self._exc


async def _drain(client: _Client) -> list[Any]:
    return [msg async for msg in safe_receive_messages(client, caller="test")]


# The verbatim text from the incident.
_REAL_MESSAGE = "Failed to authenticate: OAuth session expired and could not be refreshed"


# ── the classifier must recognise the message the SDK actually emits ──────


@pytest.mark.parametrize(
    "message",
    [
        _REAL_MESSAGE,
        "Failed to authenticate with the Claude API",
        "OAuth session expired",
        # already covered before this change; kept so the additions cannot
        # regress the originals
        "HTTP 401 Unauthorized",
        "token expired",
        "invalid token",
    ],
)
def test_authentication_failures_are_classified(message: str) -> None:
    assert is_authentication_error(RuntimeError(message)), (
        f"not recognised as an auth failure, so it is reported as a generic "
        f"error and retried: {message!r}"
    )


def test_ordinary_errors_are_not_misclassified_as_auth() -> None:
    """The guard must not swallow the resilience it is bolted onto."""
    for message in (
        "connection reset by peer",
        "unexpected message type",
        "json decode error at line 4012",
    ):
        assert not is_authentication_error(RuntimeError(message)), message


# ── the stream wrapper must not swallow a dead credential ────────────────


@pytest.mark.asyncio
async def test_auth_failure_propagates_to_the_caller() -> None:
    """#854: swallowing this is what turned a dead credential into an empty
    plan, a full retry, and a status record blaming plan validity."""
    with pytest.raises(RuntimeError, match="authenticate"):
        await _drain(_Client(RuntimeError(_REAL_MESSAGE)))


@pytest.mark.asyncio
async def test_auth_failure_propagates_even_after_partial_output() -> None:
    """Partial output does not make the credential any less dead."""
    with pytest.raises(RuntimeError):
        await _drain(_Client(RuntimeError(_REAL_MESSAGE), before=("msg-1", "msg-2")))


@pytest.mark.asyncio
async def test_transient_stream_errors_are_still_absorbed() -> None:
    """The wrapper's original job is intact: a mid-stream transport glitch
    still ends the stream gracefully with whatever was collected."""
    msgs = await _drain(
        _Client(RuntimeError("connection reset by peer"), before=("msg-1", "msg-2"))
    )
    assert msgs == ["msg-1", "msg-2"]


@pytest.mark.asyncio
async def test_clean_stream_is_unaffected() -> None:
    class _Clean:
        async def receive_response(self):
            for msg in ("a", "b"):
                yield msg

    assert [m async for m in safe_receive_messages(_Clean(), caller="test")] == [
        "a",
        "b",
    ]
