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
from dataclasses import dataclass
from pathlib import Path

from agents.flaky_history import FlakyClass, load_history

from .corpus import load_corpus
from .diff import RegressionDiff, diff_runs
from .models import RegressionRun
from .report import render_json, render_markdown
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
    logger.info(
        "regression: project=%s run=%s — %d test(s) in corpus",
        request.project_id,
        request.run_id,
        len(entries),
    )
    outcomes = run_corpus(entries, runner)

    baseline = load_baseline(reg_dir)
    run = RegressionRun(
        run_id=request.run_id,
        project_id=request.project_id,
        ran_at=request.ran_at,
        results=tuple(outcomes),
        commit=request.commit,
        target_url=request.target_url,
        baseline_run_id=baseline.run_id if baseline else None,
    )

    flaky_lookup = (
        _flaky_lookup_from_store(request.flaky_store_path)
        if request.flaky_store_path
        else None
    )
    diff = diff_runs(run, baseline, flaky_lookup=flaky_lookup)

    save_run(reg_dir, run)
    _write_reports(reg_dir, run, diff)

    logger.info(
        "regression: run=%s done — %s (regressions=%d)",
        request.run_id,
        "REGRESSIONS" if diff.has_regressions else "clean",
        len(diff.regressions),
    )
    return run, diff


def _write_reports(reg_dir: Path, run: RegressionRun, diff: RegressionDiff) -> None:
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / f"{run.run_id}-report.md").write_text(
        render_markdown(diff, run), encoding="utf-8"
    )
    (reg_dir / f"{run.run_id}-report.json").write_text(
        json.dumps(render_json(diff, run), indent=2, sort_keys=True),
        encoding="utf-8",
    )
