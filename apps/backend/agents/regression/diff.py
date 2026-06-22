"""Regression diff & classification — RFC-0018 #483 (Phase 1).

Given a *current* :class:`RegressionRun` and the *baseline* it should be
compared against, classify every test into exactly one regression class. This
is the "detection brain": it makes "did anything regress between these two
runs?" answerable without any execution.

Classification is pure: it takes the two runs plus an optional flaky-lookup
callable (so it never touches the ``flaky_history`` store directly — the
executor in RFC-0018 #484 wires the real lookup in). Precedence, highest
first:

1. ``DROPPED``   — in baseline, absent now
2. ``QUARANTINED`` — current status is quarantined (excluded from the gate)
3. ``NEW``       — absent from baseline, present now
4. ``FLAKY``     — flaky-lookup reports the test as historically flaky
5. ``REGRESSION``    — was passing, now failing
6. ``FIXED``         — was failing, now passing
7. ``STILL_FAILING`` — failing in both
8. ``STABLE_PASS``   — passing (or skipped) in both
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agents.flaky_history import FlakyClass

from .models import RegressionRun, TestStatus


class RegressionClass(str, Enum):
    REGRESSION = "regression"
    FIXED = "fixed"
    STILL_FAILING = "still_failing"
    STABLE_PASS = "stable_pass"
    FLAKY = "flaky"
    QUARANTINED = "quarantined"
    NEW = "new"
    DROPPED = "dropped"


# Classes that should fail a regression gate (a real, actionable problem).
_GATING_CLASSES = frozenset({RegressionClass.REGRESSION})


def classify(
    *,
    baseline_status: TestStatus | None,
    current_status: TestStatus | None,
    is_flaky: bool = False,
) -> RegressionClass:
    """Classify a single test from its baseline/current status.

    ``None`` means the test was absent from that run.
    """
    if current_status is None:
        return RegressionClass.DROPPED
    if current_status is TestStatus.QUARANTINED:
        return RegressionClass.QUARANTINED
    if baseline_status is None:
        return RegressionClass.NEW
    if is_flaky:
        return RegressionClass.FLAKY

    base_fail = baseline_status.is_fail
    cur_fail = current_status.is_fail
    if not base_fail and cur_fail:
        return RegressionClass.REGRESSION
    if base_fail and not cur_fail:
        return RegressionClass.FIXED
    if base_fail and cur_fail:
        return RegressionClass.STILL_FAILING
    return RegressionClass.STABLE_PASS


@dataclass(frozen=True)
class RegressionDiff:
    """The classified comparison of a run against its baseline."""

    run_id: str
    baseline_run_id: str | None
    # test_id -> RegressionClass, sorted by test_id for determinism
    entries: tuple[tuple[str, RegressionClass], ...]

    @property
    def counts(self) -> dict[str, int]:
        out = {c.value: 0 for c in RegressionClass}
        for _, cls in self.entries:
            out[cls.value] += 1
        return out

    def of_class(self, cls: RegressionClass) -> tuple[str, ...]:
        return tuple(tid for tid, c in self.entries if c is cls)

    @property
    def regressions(self) -> tuple[str, ...]:
        return self.of_class(RegressionClass.REGRESSION)

    @property
    def has_regressions(self) -> bool:
        return any(c in _GATING_CLASSES for _, c in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "baseline_run_id": self.baseline_run_id,
            "has_regressions": self.has_regressions,
            "counts": self.counts,
            "entries": {tid: cls.value for tid, cls in self.entries},
        }


def diff_runs(
    current: RegressionRun,
    baseline: RegressionRun | None,
    *,
    flaky_lookup: Callable[[str], FlakyClass] | None = None,
) -> RegressionDiff:
    """Classify *current* against *baseline*.

    ``flaky_lookup`` maps a ``test_id`` to its historical
    :class:`~agents.flaky_history.FlakyClass`; when omitted, no test is
    treated as flaky. With no baseline, every present test is ``NEW`` (or
    ``QUARANTINED``).
    """
    base_ids = set(baseline.test_ids) if baseline else set()
    all_ids = sorted(base_ids | set(current.test_ids))

    entries: list[tuple[str, RegressionClass]] = []
    for tid in all_ids:
        is_flaky = bool(flaky_lookup and flaky_lookup(tid) is FlakyClass.FLAKY)
        cls = classify(
            baseline_status=baseline.status_of(tid) if baseline else None,
            current_status=current.status_of(tid),
            is_flaky=is_flaky,
        )
        entries.append((tid, cls))

    return RegressionDiff(
        run_id=current.run_id,
        baseline_run_id=baseline.run_id if baseline else None,
        entries=tuple(entries),
    )
