"""Tests for the regression portal read-model — RFC-0018 #489 (part 1)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    QuarantineEntry,
    RegressionRequest,
    TestOutcome,
    TestStatus,
    add_to_quarantine,
    project_regression_summary,
    quarantine_path,
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


class _Runner:
    def __init__(self, statuses):
        self._statuses = statuses

    def run(self, entry: CorpusEntry) -> TestOutcome:
        return TestOutcome(
            entry.test_id, entry.lane, entry.framework, self._statuses[entry.test_id]
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


def test_summary_empty_project_is_valid(tmp_path):
    reg = regression_dir(tmp_path, "demo")
    s = project_regression_summary(reg)
    assert s["latest_run_id"] is None
    assert s["runs"] == []
    assert s["has_regressions"] is None
    assert s["coverage_trend"] == []
    assert s["quarantined"] == []


def test_summary_reflects_runs_and_regression(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run_regression(_req(tmp_path, reg, "r1"), _Runner({"a": TestStatus.PASSED}))
    run_regression(_req(tmp_path, reg, "r2"), _Runner({"a": TestStatus.FAILED}))

    s = project_regression_summary(reg)
    assert s["latest_run_id"] == "r2"
    assert s["baseline_run_id"] == "r1"
    assert [r["run_id"] for r in s["runs"]] == ["r1", "r2"]
    assert s["has_regressions"] is True
    assert s["latest_diff"]["counts"]["regression"] == 1
    assert s["latest"]["run_id"] == "r2"


def test_summary_includes_quarantine(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run_regression(_req(tmp_path, reg, "r1"), _Runner({"a": TestStatus.PASSED}))
    add_to_quarantine(
        quarantine_path(reg), QuarantineEntry("flaky", "chronic", "r1", 0.5)
    )
    s = project_regression_summary(reg)
    assert s["quarantined"] == [
        {
            "test_id": "flaky",
            "reason": "chronic",
            "since_run": "r1",
            "flip_rate": 0.5,
        }
    ]


def test_summary_is_json_serialisable(tmp_path):
    import json

    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run_regression(_req(tmp_path, reg, "r1"), _Runner({"a": TestStatus.PASSED}))
    # the portal serves this over HTTP — must round-trip through JSON
    json.dumps(project_regression_summary(reg))
