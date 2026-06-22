"""Regression orchestrator — RFC-0018 #484 (part 4).

Ties the slices together into one regression run:

  load_corpus -> run_corpus(runner) -> assemble RegressionRun
    -> diff_runs(vs baseline, with flaky lookup) -> save_run + write report

The runner and the clock (``run_id`` / ``ran_at``) are injected so the whole
flow is unit-testable with a fake runner and no cluster; the CLI (and the
scheduler, #488) supply a :class:`~agents.regression.nix_runner.NixJobRunner`
and real timestamps.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from agents.flaky_history import FlakyClass, load_history

from .corpus import load_corpus
from .coverage_trend import (
    CoveragePoint,
    DriftResult,
    compute_drift,
    coverage_trend_path,
    load_trend,
    record_coverage,
)
from .diff import RegressionDiff, diff_runs
from .impact import select_impacted
from .models import RegressionRun, TestOutcome, TestStatus
from .quarantine import quarantine_path, quarantined_ids
from .report import render_json, render_markdown
from .retry import DEFAULT_MAX_ATTEMPTS, RetryingRunner
from .runner import RegressionRunner, run_corpus
from .store import load_baseline, save_run

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegressionRequest:
    """Everything a single regression pass needs except the runner.

    ``run_id`` / ``ran_at`` are supplied by the caller (CLI / scheduler) so the
    orchestrator stays clock-free and deterministic under test.
    """

    project_id: str
    repo_root: Path
    reg_dir: Path
    run_id: str
    ran_at: str
    commit: str | None = None
    target_url: str | None = None
    lanes: tuple[str, ...] | None = None
    flaky_store_path: Path | None = None
    # Per-test attempts (within-run retry); 1 disables retry.
    retry_attempts: int = DEFAULT_MAX_ATTEMPTS
    # Impact selection: when either is set, only re-run the corpus subset
    # covering these changed AC ids / changed test files (full corpus otherwise).
    changed_acs: tuple[str, ...] | None = None
    changed_files: tuple[str, ...] | None = None


def _flaky_lookup_from_store(store_path: Path) -> Callable[[str], FlakyClass]:
    """Build a ``test_id -> FlakyClass`` lookup backed by the flaky-history store."""

    def lookup(test_id: str) -> FlakyClass:
        return load_history(store_path, test_id).classification

    return lookup


def run_regression(
    request: RegressionRequest, runner: RegressionRunner
) -> tuple[RegressionRun, RegressionDiff]:
    """Run one regression pass over the persisted corpus.

    Loads the corpus from ``request.repo_root``, runs every test through
    *runner*, assembles an immutable :class:`RegressionRun`, diffs it against
    the stored baseline (classifying flakes via the flaky-history store when
    ``request.flaky_store_path`` is set), persists the run, and writes
    ``<run_id>-report.{md,json}`` into ``request.reg_dir``.

    Returns the run and its classified diff.
    """
    reg_dir = request.reg_dir
    entries = load_corpus(request.repo_root, lanes=request.lanes)
    selected = bool(request.changed_acs or request.changed_files)
    if selected:
        entries = select_impacted(
            entries,
            changed_acs=list(request.changed_acs or ()),
            changed_files=list(request.changed_files or ()),
        )
    logger.info(
        "regression: project=%s run=%s — %d test(s)%s",
        request.project_id,
        request.run_id,
        len(entries),
        " (impact-selected)" if selected else " in corpus",
    )

    # Within-run retry: absorb transient blips before they look like failures.
    active_runner = (
        RetryingRunner(runner, request.retry_attempts)
        if request.retry_attempts > 1
        else runner
    )
    outcomes = run_corpus(entries, active_runner)

    # Cross-run quarantine: chronically-flaky tests are excluded from the gate.
    outcomes = _apply_quarantine(reg_dir, outcomes)

    baseline = load_baseline(reg_dir)
    run = RegressionRun(
        run_id=request.run_id,
        project_id=request.project_id,
        ran_at=request.ran_at,
        results=tuple(outcomes),
        commit=request.commit,
        target_url=request.target_url,
        baseline_run_id=baseline.run_id if baseline else None,
        coverage_pct=_aggregate_coverage(outcomes),
    )

    flaky_lookup = (
        _flaky_lookup_from_store(request.flaky_store_path)
        if request.flaky_store_path
        else None
    )
    # For an impact-selected (partial) run, scope the baseline to the tests we
    # actually re-ran so un-selected baseline tests aren't mis-classified as
    # "dropped". run.baseline_run_id still records the real baseline.
    diff_baseline = baseline
    if selected and baseline is not None:
        ran = set(run.test_ids)
        diff_baseline = replace(
            baseline, results=tuple(r for r in baseline.results if r.test_id in ran)
        )
    diff = diff_runs(run, diff_baseline, flaky_lookup=flaky_lookup)

    # Coverage trend: drift vs the trailing baseline, then record this point.
    drift = _coverage_drift_and_record(reg_dir, run)

    save_run(reg_dir, run)
    _write_reports(reg_dir, run, diff, drift)

    logger.info(
        "regression: run=%s done — %s (regressions=%d)",
        request.run_id,
        "REGRESSIONS" if diff.has_regressions else "clean",
        len(diff.regressions),
    )
    return run, diff


def _apply_quarantine(reg_dir: Path, outcomes: list[TestOutcome]) -> list[TestOutcome]:
    """Mark quarantined tests' outcomes as QUARANTINED (excluded from the gate).

    Reads the per-project quarantine store; a quarantined test is reported but
    never fails the run, regardless of its raw pass/fail this time.
    """
    quarantined = quarantined_ids(quarantine_path(reg_dir))
    if not quarantined:
        return outcomes
    return [
        replace(o, status=TestStatus.QUARANTINED) if o.test_id in quarantined else o
        for o in outcomes
    ]


def _aggregate_coverage(outcomes: list[TestOutcome]) -> float | None:
    """Project coverage for the run: mean of per-test coverage, or None.

    None when no outcome carries a coverage figure (e.g. browser-only runs or
    until per-test coverage is populated), in which case no trend is recorded.
    """
    pcts = [o.coverage_pct for o in outcomes if o.coverage_pct is not None]
    if not pcts:
        return None
    return round(sum(pcts) / len(pcts), 2)


def _coverage_drift_and_record(reg_dir: Path, run: RegressionRun) -> DriftResult | None:
    """Compute drift vs the trailing baseline, then record this run's point.

    Returns None when the run has no coverage figure. Drift is computed BEFORE
    recording so the new point isn't its own baseline.
    """
    if run.coverage_pct is None:
        return None
    path = coverage_trend_path(reg_dir)
    drift = compute_drift(load_trend(path), run.coverage_pct)
    record_coverage(
        path,
        CoveragePoint(
            run_id=run.run_id,
            ran_at=run.ran_at,
            coverage_pct=run.coverage_pct,
            commit=run.commit,
        ),
    )
    if drift.dropped:
        logger.warning(
            "regression: coverage dropped %.1f pts (%.1f%% -> %.1f%%) at run=%s",
            -drift.delta if drift.delta is not None else 0.0,
            drift.baseline_pct if drift.baseline_pct is not None else 0.0,
            run.coverage_pct,
            run.run_id,
        )
    return drift


def _write_reports(
    reg_dir: Path,
    run: RegressionRun,
    diff: RegressionDiff,
    drift: DriftResult | None = None,
) -> None:
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / f"{run.run_id}-report.md").write_text(
        render_markdown(diff, run, drift), encoding="utf-8"
    )
    (reg_dir / f"{run.run_id}-report.json").write_text(
        json.dumps(render_json(diff, run, drift), indent=2, sort_keys=True),
        encoding="utf-8",
    )
