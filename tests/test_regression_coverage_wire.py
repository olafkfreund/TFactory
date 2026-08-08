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


def write_cobertura(path: Path, covered: int, total: int) -> Path:
    """Write a valid Cobertura report covering *covered* of *total* lines.

    A report, not a float. Handing the orchestrator a ready-made percentage is
    what let #865 hide: every test in this file passed while the production
    runner recorded no coverage at all, because nothing here ever exercised the
    path that turns a coverage report into a number.
    """
    lines = "".join(
        f'<line number="{n}" hits="{1 if n <= covered else 0}"/>'
        for n in range(1, total + 1)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{covered / total}">'
        f"<packages><package><classes>"
        f'<class filename="app.py" name="app"><lines>{lines}</lines></class>'
        f"</classes></package></packages></coverage>",
        encoding="utf-8",
    )
    return path


class _CovRunner:
    """Passes every test, each producing a real coverage report on disk.

    Every test's report covers the SAME lines, so the union equals the per-test
    figure — which is what makes the trend/drift arithmetic below readable. The
    disjoint case (where the union and the mean diverge) is proved in
    ``test_regression_coverage_e2e.py``.
    """

    def __init__(self, coverage_pct: float | None, tmp_path: Path):
        self._cov = coverage_pct
        self._tmp = tmp_path
        self._n = 0

    def run(self, entry: CorpusEntry) -> TestOutcome:
        report = None
        if self._cov is not None:
            self._n += 1
            report = write_cobertura(
                self._tmp / "cov" / f"{self._n}.xml", int(self._cov), 100
            )
        return TestOutcome(
            entry.test_id,
            entry.lane,
            entry.framework,
            TestStatus.PASSED,
            coverage_pct=self._cov,
            coverage_report_path=str(report) if report else None,
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
    run, _diff = run_regression(_req(tmp_path, reg, "r1"), _CovRunner(80.0, tmp_path))
    assert run.coverage_pct == 80.0  # union of per-test covered lines
    # recorded to the trend ledger
    trend = load_trend(coverage_trend_path(reg))
    assert [p.run_id for p in trend] == ["r1"]
    # first run: no baseline, drift present but delta None
    report = json.loads((reg / "r1-report.json").read_text())
    assert report["drift"]["delta"] is None


def test_coverage_drop_flagged_in_report(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run_regression(_req(tmp_path, reg, "r1"), _CovRunner(85.0, tmp_path))
    run_regression(_req(tmp_path, reg, "r2"), _CovRunner(80.0, tmp_path))  # -5 pts
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
    run, _diff = run_regression(_req(tmp_path, reg, "r1"), _CovRunner(None, tmp_path))
    assert run.coverage_pct is None
    assert load_trend(coverage_trend_path(reg)) == []
    report = json.loads((reg / "r1-report.json").read_text())
    assert report["drift"] is None
