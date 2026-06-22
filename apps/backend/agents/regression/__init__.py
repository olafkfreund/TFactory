"""Regression suite & continuous verification — RFC-0018.

Phase 1 (#483): the detection brain — immutable run records, a per-project
store, and diff/classification of a run against its baseline. No execution
and no network; the re-run executor (#484) builds on top of this.
"""

from __future__ import annotations

from .cli import main as cli_main
from .corpus import CorpusEntry, group_by_lane, load_corpus
from .coverage_trend import (
    CoveragePoint,
    DriftResult,
    compute_drift,
    coverage_trend_path,
    load_trend,
    record_coverage,
)
from .diff import RegressionClass, RegressionDiff, classify, diff_runs
from .impact import (
    build_ac_index,
    select_by_acs,
    select_by_changed_files,
    select_impacted,
)
from .models import RegressionRun, TestOutcome, TestStatus
from .nix_runner import (
    NixJobRunner,
    NixSubstrateUnavailableError,
    UnsupportedFrameworkError,
    outcome_from_run_result,
)
from .orchestrator import RegressionRequest, run_regression
from .quarantine import (
    QuarantineEntry,
    add_to_quarantine,
    is_quarantined,
    load_quarantine,
    quarantine_path,
    quarantined_ids,
    release_from_quarantine,
)
from .quarantine_policy import (
    quarantine_entry_for,
    should_quarantine,
    should_release,
)
from .report import render_json, render_markdown
from .retry import RetryingRunner
from .runner import RegressionRunner, run_corpus
from .store import (
    list_runs,
    load_baseline,
    load_latest,
    load_run,
    regression_dir,
    save_run,
    set_baseline,
)
from .trigger import ProjectScheduleConfig, run_for_project

__all__ = [
    "CorpusEntry",
    "CoveragePoint",
    "DriftResult",
    "NixJobRunner",
    "NixSubstrateUnavailableError",
    "ProjectScheduleConfig",
    "QuarantineEntry",
    "RegressionClass",
    "RegressionDiff",
    "RegressionRequest",
    "RegressionRun",
    "RegressionRunner",
    "RetryingRunner",
    "TestOutcome",
    "TestStatus",
    "UnsupportedFrameworkError",
    "add_to_quarantine",
    "build_ac_index",
    "classify",
    "cli_main",
    "compute_drift",
    "coverage_trend_path",
    "diff_runs",
    "group_by_lane",
    "is_quarantined",
    "list_runs",
    "load_baseline",
    "load_corpus",
    "load_latest",
    "load_quarantine",
    "load_run",
    "load_trend",
    "outcome_from_run_result",
    "quarantine_entry_for",
    "quarantine_path",
    "quarantined_ids",
    "record_coverage",
    "regression_dir",
    "release_from_quarantine",
    "render_json",
    "render_markdown",
    "run_corpus",
    "run_for_project",
    "run_regression",
    "save_run",
    "select_by_acs",
    "select_by_changed_files",
    "select_impacted",
    "set_baseline",
    "should_quarantine",
    "should_release",
]
