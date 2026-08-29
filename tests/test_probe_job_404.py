"""A 404 from the Job probe is an ANSWER, not a gap.

`ttlSecondsAfterFinished` is 300s, so "not found" is the normal end state of a
Job that ran and went away -- and it is the exact case `reap_if_orphaned`
exists for. Folding it into the blanket `except Exception` made that branch
unreachable from production: every 404 returned (exists, active), so a Job that
died without writing a terminal row read as "still running" forever once GC'd,
and the liveness sweep -- which calls this same probe for its second signal --
reported it alive at every sweep.

The pre-existing tests only ever injected a `probe_fn` returning (False, False),
so they exercised the caller and proved nothing about the probe itself. These
drive the REAL probe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))


class _ApiError(Exception):
    """Shaped like kubernetes_asyncio.client.ApiException: it carries .status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"api error {status}")
        self.status = status


@pytest.mark.asyncio
async def test_a_404_reports_the_job_is_gone(monkeypatch):
    from agents import verify_dispatch as vd

    async def _boom():
        raise _ApiError(404)

    monkeypatch.setattr(vd, "_k8s_batch", _boom)
    exists, active, succeeded = await vd._probe_job("gone-job", "factory")

    assert exists is False, "a GC'd Job must not report as existing"
    assert active is False
    assert succeeded is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_every_other_api_failure_still_reads_as_alive(monkeypatch, status):
    """An outage, a permission error or a rate limit must NOT reap a live verify.

    This is the half that must not regress while fixing the 404: the blanket
    fail-toward-alive is correct for a probe that could not get an answer.
    """
    from agents import verify_dispatch as vd

    async def _boom():
        raise _ApiError(status)

    monkeypatch.setattr(vd, "_k8s_batch", _boom)
    exists, active, _ = await vd._probe_job("live-job", "factory")

    assert exists is True, f"HTTP {status} is a gap, not an answer"
    assert active is True


@pytest.mark.asyncio
async def test_an_exception_with_no_status_still_reads_as_alive(monkeypatch):
    """A timeout or a DNS failure carries no `.status` and must fail closed."""
    from agents import verify_dispatch as vd

    async def _boom():
        raise TimeoutError("no route to the api server")

    monkeypatch.setattr(vd, "_k8s_batch", _boom)
    exists, active, _ = await vd._probe_job("live-job", "factory")

    assert exists is True
    assert active is True
