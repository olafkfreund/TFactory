"""Tests for the regression orchestrator — RFC-0018 #484 (part 4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.flaky_history import record_outcome  # noqa: E402
from agents.regression import (  # noqa: E402
    CorpusEntry,
    RegressionClass,
    RegressionRequest,
    TestOutcome,
    TestStatus,
    load_latest,
    regression_dir,
    run_regression,
)
from tests_catalog.io import save_catalog  # noqa: E402
from tests_catalog.schema import CatalogEntry, TestsCatalog  # noqa: E402


def _catalog(repo_root: Path, *ids: str) -> None:
    entries = tuple(
        CatalogEntry.from_dict(
            {
                "test_id": tid,
                "test_file": f"tests/{tid}.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": [f"AC#1: {tid}"],
                "generated_at": "2026-06-22T12:00:00Z",
                "generated_by_task": "demo",
                "last_verdict": "accept",
            }
        )
        for tid in ids
    )
    save_catalog(
        repo_root,
        TestsCatalog(version=1, updated_at="2026-06-22T12:00:00Z", tests=entries),
    )


class _StatusRunner:
    """Returns a preset status per test_id."""

    def __init__(self, statuses: dict[str, TestStatus]):
        self._statuses = statuses

    def run(self, entry: CorpusEntry) -> TestOutcome:
        return TestOutcome(
            test_id=entry.test_id,
            lane=entry.lane,
            framework=entry.framework,
            status=self._statuses[entry.test_id],
        )


def test_first_run_is_baseline_and_writes_report(tmp_path):
    _catalog(tmp_path, "a", "b")
    reg = regression_dir(tmp_path, "demo")
    runner = _StatusRunner({"a": TestStatus.PASSED, "b": TestStatus.PASSED})

    run, diff = run_regression(
        RegressionRequest(
            project_id="demo",
            repo_root=tmp_path,
            reg_dir=reg,
            run_id="r1",
            ran_at="2026-06-22T12:00:00Z",
            commit="abc",
        ),
        runner,
    )

    assert run.totals == {
        "total": 2,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
        "quarantined": 0,
    }
    assert run.baseline_run_id is None  # first run has no baseline
    assert diff.counts["new"] == 2 and not diff.has_regressions
    # persisted + reports written
    assert load_latest(reg).run_id == "r1"
    assert (reg / "r1-report.md").is_file()
    report = json.loads((reg / "r1-report.json").read_text())
    assert report["run"]["run_id"] == "r1"


def test_second_run_detects_regression(tmp_path):
    _catalog(tmp_path, "a", "b")
    reg = regression_dir(tmp_path, "demo")
    # baseline: both pass
    run_regression(
        RegressionRequest(
            project_id="demo",
            repo_root=tmp_path,
            reg_dir=reg,
            run_id="r1",
            ran_at="2026-06-22T12:00:00Z",
        ),
        _StatusRunner({"a": TestStatus.PASSED, "b": TestStatus.PASSED}),
    )
    # now 'b' breaks
    _run, diff = run_regression(
        RegressionRequest(
            project_id="demo",
            repo_root=tmp_path,
            reg_dir=reg,
            run_id="r2",
            ran_at="2026-06-22T13:00:00Z",
        ),
        _StatusRunner({"a": TestStatus.PASSED, "b": TestStatus.FAILED}),
    )
    assert diff.baseline_run_id == "r1"
    assert diff.regressions == ("b",)
    assert diff.has_regressions is True


def test_flaky_store_classifies_flaky_over_regression(tmp_path):
    _catalog(tmp_path, "flip")
    reg = regression_dir(tmp_path, "demo")
    history = tmp_path / "test_history.json"
    # seed an alternating (flaky) history so flip-rate >= threshold
    for passed in (True, False, True, False, True):
        record_outcome(history, "flip", passed)

    # baseline pass, current fail — would be a regression, but history says flaky
    run_regression(
        RegressionRequest(
            project_id="demo",
            repo_root=tmp_path,
            reg_dir=reg,
            run_id="r1",
            ran_at="2026-06-22T12:00:00Z",
            flaky_store_path=history,
        ),
        _StatusRunner({"flip": TestStatus.PASSED}),
    )
    _run, diff = run_regression(
        RegressionRequest(
            project_id="demo",
            repo_root=tmp_path,
            reg_dir=reg,
            run_id="r2",
            ran_at="2026-06-22T13:00:00Z",
            flaky_store_path=history,
        ),
        _StatusRunner({"flip": TestStatus.FAILED}),
    )
    assert dict(diff.entries)["flip"] is RegressionClass.FLAKY
    assert not diff.has_regressions


def test_no_corpus_is_clean_empty_run(tmp_path):
    reg = regression_dir(tmp_path, "demo")
    run, diff = run_regression(
        RegressionRequest(
            project_id="demo",
            repo_root=tmp_path,
            reg_dir=reg,
            run_id="r1",
            ran_at="2026-06-22T12:00:00Z",
        ),
        _StatusRunner({}),
    )
    assert run.totals["total"] == 0
    assert not diff.has_regressions
    assert (reg / "r1-report.md").is_file()
