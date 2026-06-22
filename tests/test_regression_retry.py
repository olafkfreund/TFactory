"""Tests for the retry-on-transient policy — RFC-0018 #485 (part 3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    RegressionRunner,
    RetryingRunner,
    TestOutcome,
    TestStatus,
)


def _entry() -> CorpusEntry:
    return CorpusEntry("t", "tests/t.py", "pytest", "unit", "python")


def _outcome(status: TestStatus) -> TestOutcome:
    return TestOutcome("t", "unit", "pytest", status)


class _ScriptedRunner:
    """Yields outcomes/exceptions from a script, one per .run() call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def run(self, entry: CorpusEntry) -> TestOutcome:
        item = self._script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _outcome(item)


def test_is_a_regression_runner():
    assert isinstance(RetryingRunner(_ScriptedRunner([])), RegressionRunner)


def test_pass_first_try_no_retry():
    inner = _ScriptedRunner([TestStatus.PASSED])
    out = RetryingRunner(inner, max_attempts=2).run(_entry())
    assert out.status is TestStatus.PASSED
    assert inner.calls == 1  # did not retry a pass


def test_fail_then_pass_absorbs_transient():
    inner = _ScriptedRunner([TestStatus.FAILED, TestStatus.PASSED])
    out = RetryingRunner(inner, max_attempts=2).run(_entry())
    assert out.status is TestStatus.PASSED
    assert inner.calls == 2


def test_fail_all_attempts_returns_failure():
    inner = _ScriptedRunner([TestStatus.FAILED, TestStatus.FAILED])
    out = RetryingRunner(inner, max_attempts=2).run(_entry())
    assert out.status is TestStatus.FAILED
    assert inner.calls == 2


def test_raise_then_pass_is_retried():
    inner = _ScriptedRunner([RuntimeError("blip"), TestStatus.PASSED])
    out = RetryingRunner(inner, max_attempts=2).run(_entry())
    assert out.status is TestStatus.PASSED
    assert inner.calls == 2


def test_all_attempts_raise_propagates():
    inner = _ScriptedRunner([RuntimeError("a"), RuntimeError("b")])
    with pytest.raises(RuntimeError):
        RetryingRunner(inner, max_attempts=2).run(_entry())
    assert inner.calls == 2


def test_real_failure_preferred_over_exception():
    # one attempt fails (real outcome), the other raises -> return the failure
    inner = _ScriptedRunner([TestStatus.FAILED, RuntimeError("blip")])
    out = RetryingRunner(inner, max_attempts=2).run(_entry())
    assert out.status is TestStatus.FAILED


def test_max_attempts_one_means_no_retry():
    inner = _ScriptedRunner([TestStatus.FAILED, TestStatus.PASSED])
    out = RetryingRunner(inner, max_attempts=1).run(_entry())
    assert out.status is TestStatus.FAILED
    assert inner.calls == 1
