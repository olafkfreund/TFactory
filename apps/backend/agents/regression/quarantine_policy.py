"""Quarantine policy — RFC-0018 #485 (part 2).

Pure decisions over a test's :class:`~agents.flaky_history.FlakyHistory`:
when to quarantine a chronically flaky test (out of the regression gate) and
when it has stabilised enough to auto-release. The orchestrator (#485 part 4)
applies these against the quarantine store; keeping them pure makes the
thresholds trivially testable.

A test is quarantine-eligible only with *enough* history — we never quarantine
on a thin record (the flaky-history classifier already flips FLAKY at 2 runs,
which is too eager for excluding a test from the gate).
"""

from __future__ import annotations

from agents.flaky_history import FlakyClass, FlakyHistory

from .quarantine import QuarantineEntry

# Minimum recorded runs before a flaky test may be quarantined. Higher than the
# classifier's MIN_RUNS_FOR_VERDICT (2) so we don't quarantine on thin history.
DEFAULT_QUARANTINE_MIN_RUNS = 5


def should_quarantine(
    history: FlakyHistory, *, min_runs: int = DEFAULT_QUARANTINE_MIN_RUNS
) -> bool:
    """True when *history* is FLAKY over at least *min_runs* recorded runs."""
    return history.runs >= min_runs and history.classification is FlakyClass.FLAKY


def should_release(
    history: FlakyHistory, *, min_runs: int = DEFAULT_QUARANTINE_MIN_RUNS
) -> bool:
    """True when a quarantined test has stabilised (STABLE over enough runs).

    Lets the orchestrator auto-release a test that has recovered, without
    waiting for an operator.
    """
    return history.runs >= min_runs and history.classification is FlakyClass.STABLE


def quarantine_entry_for(history: FlakyHistory, *, run_id: str) -> QuarantineEntry:
    """Build the :class:`QuarantineEntry` recording why a flaky test was quarantined."""
    return QuarantineEntry(
        test_id=history.test_id,
        reason=(f"flaky: flip_rate {history.flip_rate:.2f} over {history.runs} runs"),
        since_run=run_id,
        flip_rate=round(history.flip_rate, 4),
    )
