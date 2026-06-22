"""Tests for the programmatic regression trigger — RFC-0018 #488 (part 1)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    ProjectScheduleConfig,
    TestOutcome,
    TestStatus,
    load_latest,
    regression_dir,
    run_for_project,
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
    def __init__(self, statuses):
        self._statuses = statuses

    def run(self, entry: CorpusEntry) -> TestOutcome:
        return TestOutcome(
            entry.test_id, entry.lane, entry.framework, self._statuses[entry.test_id]
        )


def test_run_for_project_uses_injected_runner_and_clock(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _catalog(repo, "a")
    ws = tmp_path / "ws"
    cfg = ProjectScheduleConfig(
        project_id="demo", repo_root=repo, workspace_root=ws, commit="abc"
    )
    run, diff = run_for_project(
        cfg,
        runner=_StatusRunner({"a": TestStatus.PASSED}),
        now=datetime(2026, 6, 22, 13, 5, 9, tzinfo=UTC),
    )
    # deterministic run id from the injected clock
    assert run.run_id == "run-20260622T130509Z"
    assert run.commit == "abc"
    # persisted under <workspace>/<project>/regression
    reg = regression_dir(ws, "demo")
    assert load_latest(reg).run_id == run.run_id
    assert (reg / f"{run.run_id}-report.md").is_file()
    assert not diff.has_regressions


def test_run_for_project_detects_regression_across_two_triggers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _catalog(repo, "a")
    ws = tmp_path / "ws"
    cfg = ProjectScheduleConfig(project_id="demo", repo_root=repo, workspace_root=ws)
    run_for_project(
        cfg,
        runner=_StatusRunner({"a": TestStatus.PASSED}),
        now=datetime(2026, 6, 22, 1, 0, 0, tzinfo=UTC),
    )
    _run, diff = run_for_project(
        cfg,
        runner=_StatusRunner({"a": TestStatus.FAILED}),
        now=datetime(2026, 6, 23, 1, 0, 0, tzinfo=UTC),
    )
    assert diff.regressions == ("a",)
    assert diff.has_regressions
