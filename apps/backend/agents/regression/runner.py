"""Regression runner seam — RFC-0018 #484 (part 2).

Defines the boundary between the regression *orchestration* (pure: load the
corpus, run it, diff, report) and the *execution* of an individual test. A
:class:`RegressionRunner` knows how to run one :class:`CorpusEntry` and return
a :class:`TestOutcome`; the real implementation (part 3) dispatches to the
Nix-flake-per-task k8s Job substrate (parity with AIFactory). Keeping this an
injected protocol lets the orchestrator be unit-tested with a fake runner and
never touch a cluster.

:func:`run_corpus` is the pure driver: it runs every entry through the runner
and **isolates failures per-test** — a runner that raises on one test yields an
``ERROR`` outcome for that test rather than aborting the whole regression run.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from .corpus import CorpusEntry
from .models import TestOutcome, TestStatus

logger = logging.getLogger(__name__)


@runtime_checkable
class RegressionRunner(Protocol):
    """Runs a single corpus entry and returns its outcome.

    Implementations MUST NOT raise for an ordinary test failure — they return a
    ``TestOutcome`` with a failing status. Raising is reserved for genuinely
    unexpected execution faults; :func:`run_corpus` converts those into an
    ``ERROR`` outcome so one bad test never aborts the run.
    """

    def run(self, entry: CorpusEntry) -> TestOutcome: ...


def run_corpus(
    entries: list[CorpusEntry], runner: RegressionRunner
) -> list[TestOutcome]:
    """Run every *entry* through *runner*, isolating per-test faults.

    Returns one :class:`TestOutcome` per entry, in input order. If
    ``runner.run`` raises for an entry, that entry is recorded as ``ERROR``
    (logged) and the run continues — robustness over fail-fast for a
    regression sweep.
    """
    outcomes: list[TestOutcome] = []
    for entry in entries:
        try:
            outcomes.append(runner.run(entry))
        except Exception:
            logger.exception(
                "regression runner raised for test_id=%s (lane=%s); recording ERROR",
                entry.test_id,
                entry.lane,
            )
            outcomes.append(
                TestOutcome(
                    test_id=entry.test_id,
                    lane=entry.lane,
                    framework=entry.framework,
                    status=TestStatus.ERROR,
                )
            )
    return outcomes
