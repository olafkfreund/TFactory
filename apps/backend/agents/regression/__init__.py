"""Regression suite & continuous verification — RFC-0018.

Phase 1 (#483): the detection brain — immutable run records, a per-project
store, and diff/classification of a run against its baseline. No execution
and no network; the re-run executor (#484) builds on top of this.
"""

from __future__ import annotations

from .corpus import CorpusEntry, group_by_lane, load_corpus
from .diff import RegressionClass, RegressionDiff, classify, diff_runs
from .models import RegressionRun, TestOutcome, TestStatus
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
    "RegressionClass",
    "RegressionDiff",
    "RegressionRun",
    "RegressionRunner",
    "TestOutcome",
    "TestStatus",
    "classify",
    "diff_runs",
    "group_by_lane",
    "list_runs",
    "load_baseline",
    "load_corpus",
    "load_latest",
    "load_run",
    "regression_dir",
    "render_json",
    "render_markdown",
    "run_corpus",
    "save_run",
    "set_baseline",
]
