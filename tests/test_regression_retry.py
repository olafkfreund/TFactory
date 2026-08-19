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


def test_the_propagated_exception_is_the_last_real_one():
    """Guards the py/illegal-raise fix (CodeQL 1599).

    `raise last_exc` carried a `# type: ignore[misc]` because `last_exc` is
    `Exception | None` and CodeQL saw `raise None` -> TypeError. It is in fact
    unreachable with None (`attempts >= 1`, and the only way out of the loop
    without returning is the except branch), so the fix states that in code
    instead of silencing it -- and the risk of that fix is substituting a
    placeholder for the real exception. Pin the identity.

    `test_all_attempts_raise_propagates` cannot catch that: its
    `pytest.raises(RuntimeError)` also matches the fallback.
    """
    last = RuntimeError("second")
    inner = _ScriptedRunner([RuntimeError("first"), last])
    with pytest.raises(RuntimeError) as excinfo:
        RetryingRunner(inner, max_attempts=2).run(_entry())

    assert excinfo.value is last
    assert str(excinfo.value) == "second"


def test_non_positive_max_attempts_still_runs_once():
    """`attempts = max(1, self.max_attempts)` is what makes the None branch
    unreachable. If that floor is ever removed, the loop body is skipped and
    the fix's explicit error fires instead of `TypeError: exceptions must
    derive from BaseException`."""
    inner = _ScriptedRunner([TestStatus.PASSED])
    out = RetryingRunner(inner, max_attempts=0).run(_entry())
    assert out.status is TestStatus.PASSED
    assert inner.calls == 1
