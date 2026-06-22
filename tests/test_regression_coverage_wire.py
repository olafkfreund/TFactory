"""Orchestrator coverage trend + drift wiring — RFC-0018 #486 (part 2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    RegressionRequest,
    TestOutcome,
    TestStatus,
    coverage_trend_path,
    load_trend,
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


class _CovRunner:
    """Passes every test, reporting a fixed per-test coverage_pct."""

    def __init__(self, coverage_pct: float | None):
        self._cov = coverage_pct

    def run(self, entry: CorpusEntry) -> TestOutcome:
        return TestOutcome(
            entry.test_id,
            entry.lane,
            entry.framework,
            TestStatus.PASSED,
            coverage_pct=self._cov,
        )


def _req(tmp_path, reg, run_id) -> RegressionRequest:
    return RegressionRequest(
        project_id="demo",
        repo_root=tmp_path,
        reg_dir=reg,
        run_id=run_id,
        ran_at="2026-06-22T12:00:00Z",
        retry_attempts=1,
    )


def test_coverage_recorded_and_reported(tmp_path):
    _catalog(tmp_path, "a", "b")
    reg = regression_dir(tmp_path, "demo")
    run, _diff = run_regression(_req(tmp_path, reg, "r1"), _CovRunner(80.0))
    assert run.coverage_pct == 80.0  # mean of per-test coverage
    # recorded to the trend ledger
    trend = load_trend(coverage_trend_path(reg))
    assert [p.run_id for p in trend] == ["r1"]
    # first run: no baseline, drift present but delta None
    report = json.loads((reg / "r1-report.json").read_text())
    assert report["drift"]["delta"] is None


def test_coverage_drop_flagged_in_report(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run_regression(_req(tmp_path, reg, "r1"), _CovRunner(85.0))
    run_regression(_req(tmp_path, reg, "r2"), _CovRunner(80.0))  # -5 pts
    report = json.loads((reg / "r2-report.json").read_text())
    assert report["drift"]["dropped"] is True
    assert report["drift"]["delta"] == -5.0
    md = (reg / "r2-report.md").read_text()
    assert "COVERAGE DROPPED" in md
    # both points recorded
    assert [p.run_id for p in load_trend(coverage_trend_path(reg))] == ["r1", "r2"]


def test_no_coverage_means_no_trend_no_drift(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run, _diff = run_regression(_req(tmp_path, reg, "r1"), _CovRunner(None))
    assert run.coverage_pct is None
    assert load_trend(coverage_trend_path(reg)) == []
    report = json.loads((reg / "r1-report.json").read_text())
    assert report["drift"] is None
