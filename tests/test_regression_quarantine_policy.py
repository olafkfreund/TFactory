"""Tests for the quarantine policy — RFC-0018 #485 (part 2)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.flaky_history import FlakyHistory  # noqa: E402
from agents.regression import (  # noqa: E402
    quarantine_entry_for,
    should_quarantine,
    should_release,
)
from agents.regression.quarantine_policy import (  # noqa: E402
    DEFAULT_QUARANTINE_MIN_RUNS,
)


def _alternating(n: int) -> tuple[bool, ...]:
    # flip every run -> flip_rate ~1.0 -> FLAKY
    return tuple(i % 2 == 0 for i in range(n))


def test_quarantine_requires_enough_runs():
    # alternating but only 4 runs (< default 5): not yet
    assert should_quarantine(FlakyHistory("t", _alternating(4))) is False
    # 6 alternating runs: flaky AND enough history
    assert should_quarantine(FlakyHistory("t", _alternating(6))) is True


def test_quarantine_false_for_stable():
    stable = FlakyHistory("t", tuple([True] * 8))
    assert should_quarantine(stable) is False


def test_min_runs_is_tunable():
    h = FlakyHistory("t", _alternating(4))
    assert should_quarantine(h, min_runs=3) is True
    assert should_quarantine(h, min_runs=10) is False


def test_should_release_when_stable_enough():
    assert should_release(FlakyHistory("t", tuple([True] * 6))) is True
    # still flaky -> don't release
    assert should_release(FlakyHistory("t", _alternating(6))) is False
    # too little history -> don't release
    assert should_release(FlakyHistory("t", (True, True))) is False


def test_default_min_runs_constant():
    assert DEFAULT_QUARANTINE_MIN_RUNS >= 3


def test_quarantine_entry_records_reason():
    h = FlakyHistory("flip", _alternating(6))
    entry = quarantine_entry_for(h, run_id="r9")
    assert entry.test_id == "flip"
    assert entry.since_run == "r9"
    assert entry.flip_rate is not None and entry.flip_rate > 0.25
    assert "flaky" in entry.reason and "flip_rate" in entry.reason
