"""Orchestrator retry + quarantine wiring — RFC-0018 #485 (part 4)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    QuarantineEntry,
    RegressionClass,
    RegressionRequest,
    TestOutcome,
    TestStatus,
    add_to_quarantine,
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


class _FlakyOnceRunner:
    """Fails the first call per test_id, passes thereafter (a transient blip)."""

    def __init__(self):
        self._seen: set[str] = set()

    def run(self, entry: CorpusEntry) -> TestOutcome:
        status = TestStatus.PASSED if entry.test_id in self._seen else TestStatus.FAILED
        self._seen.add(entry.test_id)
        return TestOutcome(entry.test_id, entry.lane, entry.framework, status)


class _StatusRunner:
    def __init__(self, statuses):
        self._statuses = statuses

    def run(self, entry: CorpusEntry) -> TestOutcome:
        return TestOutcome(
            entry.test_id, entry.lane, entry.framework, self._statuses[entry.test_id]
        )


def _req(tmp_path, reg, **kw) -> RegressionRequest:
    return RegressionRequest(
        project_id="demo",
        repo_root=tmp_path,
        reg_dir=reg,
        run_id=kw.pop("run_id", "r1"),
        ran_at="2026-06-22T12:00:00Z",
        **kw,
    )


def test_retry_absorbs_transient_failure(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    # retry_attempts default (2): fail-then-pass -> PASSED, no regression
    run, diff = run_regression(_req(tmp_path, reg), _FlakyOnceRunner())
    assert run.totals["passed"] == 1
    assert run.totals["failed"] == 0
    assert not diff.has_regressions


def test_retry_disabled_keeps_failure(tmp_path):
    _catalog(tmp_path, "a")
    reg = regression_dir(tmp_path, "demo")
    run, _diff = run_regression(
        _req(tmp_path, reg, retry_attempts=1), _FlakyOnceRunner()
    )
    assert run.totals["failed"] == 1  # no retry -> the first-call failure stands


def test_quarantined_test_excluded_from_gate(tmp_path):
    _catalog(tmp_path, "good", "flaky")
    reg = regression_dir(tmp_path, "demo")
    # establish a clean baseline first (both pass), no quarantine yet
    run_regression(
        _req(tmp_path, reg, run_id="r1"),
        _StatusRunner({"good": TestStatus.PASSED, "flaky": TestStatus.PASSED}),
    )
    # quarantine 'flaky', then it fails -> must NOT be a regression
    add_to_quarantine(
        quarantine_path(reg), QuarantineEntry("flaky", "chronic", "r1", 0.5)
    )
    run, diff = run_regression(
        _req(tmp_path, reg, run_id="r2", retry_attempts=1),
        _StatusRunner({"good": TestStatus.PASSED, "flaky": TestStatus.FAILED}),
    )
    assert run.totals["quarantined"] == 1
    assert not diff.has_regressions
    assert dict(diff.entries)["flaky"] is RegressionClass.QUARANTINED
