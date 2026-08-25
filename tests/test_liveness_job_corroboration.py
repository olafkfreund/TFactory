"""A quiet run whose Job is still running is not stalled.

TFactory#1173: `evaluate_liveness` measured `status.json`'s `updated_at` alone --
a HEARTBEAT, not liveness. Spec 165 was flagged `watchdog_stalled` while its lane
Job had been Running for 26 minutes and had written 44 screenshots and 41 videos;
its browser lane finished 20 tests with 0 failures. The evaluator only writes
status at phase boundaries, so a long lane phase is indistinguishable from death.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from agents.liveness import evaluate_liveness


def _spec(tmp_path, *, idle_seconds: float, status: str = "evaluating"):
    now = datetime(2026, 8, 25, 17, 30, tzinfo=UTC)
    updated = now - timedelta(seconds=idle_seconds)
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "status": status,
                "phase": "evaluator_initial_started",
                "updated_at": updated.isoformat(),
            }
        )
    )
    return tmp_path, now


def test_quiet_but_job_running_is_not_stalled(tmp_path):
    """The spec-165 case: 26 minutes silent, Job alive, real work landing."""
    spec, now = _spec(tmp_path, idle_seconds=1560)
    v = evaluate_liveness(spec, now=now, deadline_seconds=600, job_active=lambda: True)
    assert v.stalled is False
    assert "still running" in v.reason


def test_quiet_and_job_gone_is_stalled(tmp_path):
    """Corroboration must not defang the watchdog: no Job means it really is dead."""
    spec, now = _spec(tmp_path, idle_seconds=1560)
    v = evaluate_liveness(spec, now=now, deadline_seconds=600, job_active=lambda: False)
    assert v.stalled is True


def test_without_the_predicate_behaviour_is_unchanged(tmp_path):
    """Omitting job_active keeps timestamp-only semantics, so no caller shifts."""
    spec, now = _spec(tmp_path, idle_seconds=1560)
    assert evaluate_liveness(spec, now=now, deadline_seconds=600).stalled is True


def test_a_fresh_heartbeat_never_consults_the_job(tmp_path):
    """Inside the deadline nothing is stalled, and no k8s call should be made."""
    spec, now = _spec(tmp_path, idle_seconds=5)

    def _boom() -> bool:  # pragma: no cover - must not be called
        raise AssertionError("job_active consulted for a healthy heartbeat")

    assert (
        evaluate_liveness(spec, now=now, deadline_seconds=600, job_active=_boom).stalled
        is False
    )
