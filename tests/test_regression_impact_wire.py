"""Orchestrator + CLI impact-selection wiring — RFC-0018 #487 (part 2)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    RegressionClass,
    RegressionRequest,
    TestOutcome,
    TestStatus,
    cli,  # noqa: E402
    regression_dir,
    run_regression,
)
from tests_catalog.io import save_catalog  # noqa: E402
from tests_catalog.schema import CatalogEntry, TestsCatalog  # noqa: E402


def _catalog(repo_root: Path) -> None:
    entries = (
        CatalogEntry.from_dict(
            {
                "test_id": "login",
                "test_file": "tests/login.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": ["AC#1: login"],
                "generated_at": "2026-06-22T12:00:00Z",
                "generated_by_task": "demo",
                "last_verdict": "accept",
            }
        ),
        CatalogEntry.from_dict(
            {
                "test_id": "profile",
                "test_file": "tests/profile.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": ["AC#2: profile"],
                "generated_at": "2026-06-22T12:00:00Z",
                "generated_by_task": "demo",
                "last_verdict": "accept",
            }
        ),
    )
    save_catalog(
        repo_root,
        TestsCatalog(version=1, updated_at="2026-06-22T12:00:00Z", tests=entries),
    )


class _StatusRunner:
    def __init__(self, statuses):
        self._statuses = statuses

    def run(self, entry: CorpusEntry) -> TestOutcome:
        return TestOutcome(
            entry.test_id, entry.lane, entry.framework, self._statuses[entry.test_id]
        )


def _req(tmp_path, reg, run_id, **kw) -> RegressionRequest:
    return RegressionRequest(
        project_id="demo",
        repo_root=tmp_path,
        reg_dir=reg,
        run_id=run_id,
        ran_at="2026-06-22T12:00:00Z",
        retry_attempts=1,
        **kw,
    )


def test_selection_runs_only_covering_tests(tmp_path):
    _catalog(tmp_path)
    reg = regression_dir(tmp_path, "demo")
    run, _diff = run_regression(
        _req(tmp_path, reg, "r1", changed_acs=("AC#1",)),
        _StatusRunner({"login": TestStatus.PASSED}),  # profile must NOT run
    )
    assert set(run.test_ids) == {"login"}  # only the covering test ran


def test_partial_run_does_not_mark_unselected_as_dropped(tmp_path):
    _catalog(tmp_path)
    reg = regression_dir(tmp_path, "demo")
    # full baseline: both pass
    run_regression(
        _req(tmp_path, reg, "r1"),
        _StatusRunner({"login": TestStatus.PASSED, "profile": TestStatus.PASSED}),
    )
    # impact run touching only AC#1 -> only 'login' re-runs
    _run, diff = run_regression(
        _req(tmp_path, reg, "r2", changed_acs=("AC#1",)),
        _StatusRunner({"login": TestStatus.PASSED}),
    )
    classes = dict(diff.entries)
    assert "profile" not in classes  # NOT classified as dropped
    assert classes["login"] is RegressionClass.STABLE_PASS
    assert not diff.has_regressions


def test_partial_run_still_detects_regression(tmp_path):
    _catalog(tmp_path)
    reg = regression_dir(tmp_path, "demo")
    run_regression(
        _req(tmp_path, reg, "r1"),
        _StatusRunner({"login": TestStatus.PASSED, "profile": TestStatus.PASSED}),
    )
    _run, diff = run_regression(
        _req(tmp_path, reg, "r2", changed_acs=("AC#1",)),
        _StatusRunner({"login": TestStatus.FAILED}),
    )
    assert diff.regressions == ("login",)
    assert diff.has_regressions


def test_cli_parses_select_args(tmp_path):
    args = cli._build_parser().parse_args(
        [
            "run",
            "--project",
            "p",
            "--repo-root",
            str(tmp_path),
            "--workspace",
            str(tmp_path / "ws"),
            "--changed-acs",
            "AC#1,AC#2",
            "--changed-files",
            "tests/a.py",
        ]
    )
    req = cli.build_request(args, run_id="rX", ran_at="2026-06-22T00:00:00Z")
    assert req.changed_acs == ("AC#1", "AC#2")
    assert req.changed_files == ("tests/a.py",)
