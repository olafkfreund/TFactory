"""#792: the gen_functional agent session is wall-clock bounded.

The timeout logic lives in the pure ``_run_bounded`` helper so these tests are
immune to the suite-wide SDK-session mock (which stubs ``_invoke_session``
wholesale) — they exercise the real ceiling behaviour directly.
"""

import asyncio

from agents import gen_functional


async def test_run_bounded_returns_none_on_timeout():
    """A coroutine that outlives the ceiling is cancelled and yields None."""

    async def _hang():
        await asyncio.sleep(30)
        return ("complete", "resp", {})

    result = await gen_functional._run_bounded(_hang(), timeout_s=0.05)
    assert result is None


async def test_run_bounded_passes_result_when_fast():
    """A coroutine that finishes under the ceiling returns its result unchanged."""

    async def _ok():
        return ("complete", "the-response", {"tokens": 1})

    result = await gen_functional._run_bounded(_ok(), timeout_s=5.0)
    assert result == ("complete", "the-response", {"tokens": 1})
