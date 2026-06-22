"""Regression suite & continuous verification — RFC-0018.

Phase 1 (#483): the detection brain — immutable run records, a per-project
store, and diff/classification of a run against its baseline. No execution
and no network; the re-run executor (#484) builds on top of this.
"""

from __future__ import annotations

from .cli import main as cli_main
from .corpus import CorpusEntry, group_by_lane, load_corpus
from .diff import RegressionClass, RegressionDiff, classify, diff_runs
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
from .report import render_json, render_markdown
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

__all__ = [
    "CorpusEntry",
    "NixJobRunner",
    "NixSubstrateUnavailableError",
    "QuarantineEntry",
    "RegressionClass",
    "RegressionDiff",
    "RegressionRequest",
    "RegressionRun",
    "RegressionRunner",
    "TestOutcome",
    "TestStatus",
    "UnsupportedFrameworkError",
    "add_to_quarantine",
    "classify",
    "cli_main",
    "diff_runs",
    "group_by_lane",
    "is_quarantined",
    "list_runs",
    "load_baseline",
    "load_corpus",
    "load_latest",
    "load_quarantine",
    "load_run",
    "outcome_from_run_result",
    "quarantine_path",
    "quarantined_ids",
    "regression_dir",
    "release_from_quarantine",
    "render_json",
    "render_markdown",
    "run_corpus",
    "run_regression",
    "save_run",
    "set_baseline",
]
