"""Tests for the regression runner seam — RFC-0018 #484 (part 2)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    RegressionRunner,
    TestOutcome,
    TestStatus,
    run_corpus,
)


def _entry(test_id: str, lane: str = "unit") -> CorpusEntry:
    return CorpusEntry(
        test_id=test_id,
        test_file=f"tests/{test_id}.py",
        framework="pytest",
        lane=lane,
        language="python",
    )


class _MapRunner:
    """Returns a preset status per test_id; raises for ids mapped to an error."""

    def __init__(self, statuses: dict[str, TestStatus], *, raise_on: set[str] = frozenset()):
        self._statuses = statuses
        self._raise_on = raise_on

    def run(self, entry: CorpusEntry) -> TestOutcome:
        if entry.test_id in self._raise_on:
            raise RuntimeError(f"boom for {entry.test_id}")
        return TestOutcome(
            test_id=entry.test_id,
            lane=entry.lane,
            framework=entry.framework,
            status=self._statuses[entry.test_id],
        )


def test_fake_runner_satisfies_protocol():
    assert isinstance(_MapRunner({}), RegressionRunner)


def test_run_corpus_collects_outcomes_in_order():
    entries = [_entry("a"), _entry("b"), _entry("c")]
    runner = _MapRunner(
        {"a": TestStatus.PASSED, "b": TestStatus.FAILED, "c": TestStatus.PASSED}
    )
    outcomes = run_corpus(entries, runner)
    assert [o.test_id for o in outcomes] == ["a", "b", "c"]
    assert [o.status for o in outcomes] == [
        TestStatus.PASSED,
        TestStatus.FAILED,
        TestStatus.PASSED,
    ]


def test_run_corpus_isolates_raising_test_as_error():
    entries = [_entry("ok"), _entry("bad"), _entry("ok2")]
    runner = _MapRunner(
        {"ok": TestStatus.PASSED, "ok2": TestStatus.PASSED}, raise_on={"bad"}
    )
    outcomes = run_corpus(entries, runner)
    # the raising test does not abort the sweep; it becomes ERROR
    by_id = {o.test_id: o.status for o in outcomes}
    assert by_id == {
        "ok": TestStatus.PASSED,
        "bad": TestStatus.ERROR,
        "ok2": TestStatus.PASSED,
    }
    bad = next(o for o in outcomes if o.test_id == "bad")
    assert bad.lane == "unit" and bad.framework == "pytest"


def test_run_corpus_empty():
    assert run_corpus([], _MapRunner({})) == []
