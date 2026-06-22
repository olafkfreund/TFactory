"""Retry-on-transient policy — RFC-0018 #485 (part 3).

A :class:`RegressionRunner` decorator that re-runs a test when it fails,
absorbing a transient blip within a single regression sweep: a test that
passes on any attempt is recorded ``PASSED``; only a test that fails every
attempt keeps its failing outcome. This complements (does not replace) the
cross-run flaky quarantine — retry handles *within-run* flakiness.

Composes with any runner, e.g. ``RetryingRunner(NixJobRunner(...))``, and is
itself a ``RegressionRunner`` so it drops straight into ``run_corpus``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .corpus import CorpusEntry
from .models import TestOutcome
from .runner import RegressionRunner

logger = logging.getLogger(__name__)

# Total attempts per test (1 = no retry; 2 = one retry on a transient failure).
DEFAULT_MAX_ATTEMPTS = 2


@dataclass
class RetryingRunner:
    """Wrap *inner*, retrying a failing/raising test up to ``max_attempts``."""

    inner: RegressionRunner
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def run(self, entry: CorpusEntry) -> TestOutcome:
        attempts = max(1, self.max_attempts)
        last_outcome: TestOutcome | None = None
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                outcome = self.inner.run(entry)
            except Exception as exc:  # noqa: BLE001 — retry, then re-raise the last
                last_exc = exc
                logger.info(
                    "retry %d/%d: test_id=%s raised %s",
                    attempt,
                    attempts,
                    entry.test_id,
                    type(exc).__name__,
                )
                continue
            if outcome.status.is_pass:
                if attempt > 1:
                    logger.info(
                        "test_id=%s passed on attempt %d/%d (transient failure absorbed)",
                        entry.test_id,
                        attempt,
                        attempts,
                    )
                return outcome
            last_outcome = outcome

        # Prefer a real failing outcome over re-raising an execution fault.
        if last_outcome is not None:
            return last_outcome
        # Every attempt raised — propagate so run_corpus records an ERROR.
        raise last_exc  # type: ignore[misc]
