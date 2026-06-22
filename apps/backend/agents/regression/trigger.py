"""Programmatic regression trigger — RFC-0018 #488 (part 1).

The shared entry point every *surface* uses to kick a regression run for a
project: the CLI, the MCP tool + HTTP endpoint (#488 part 3), and the nightly
k8s CronJob / GitHub Actions schedule (#488 part 2). It resolves the per-project
workspace, generates the run id / timestamp, builds the Nix-Job runner, and
delegates to :func:`run_regression`.

The runner and clock are injectable so this is unit-testable with a fake runner
and a fixed time, never touching a cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cli import now_run_id
from .diff import RegressionDiff
from .models import RegressionRun
from .nix_runner import NixJobRunner
from .orchestrator import RegressionRequest, run_regression
from .runner import RegressionRunner
from .store import regression_dir


@dataclass(frozen=True)
class ProjectScheduleConfig:
    """Per-project inputs for a scheduled / triggered regression run."""

    project_id: str
    repo_root: Path
    workspace_root: Path
    spec_dir: Path | None = None
    commit: str | None = None
    target_url: str | None = None
    lanes: tuple[str, ...] | None = None
    flaky_store_path: Path | None = None


def _runner_for(config: ProjectScheduleConfig) -> RegressionRunner:
    extra_env = (
        {"TFACTORY_TARGET_URL": config.target_url} if config.target_url else None
    )
    return NixJobRunner(
        spec_dir=config.spec_dir or config.repo_root,
        project_dir=config.repo_root,
        extra_env=extra_env,
    )


def run_for_project(
    config: ProjectScheduleConfig,
    *,
    runner: RegressionRunner | None = None,
    now: datetime | None = None,
) -> tuple[RegressionRun, RegressionDiff]:
    """Kick a regression run for *config*; returns the run and its diff.

    Generates the run id / timestamp, resolves ``<workspace>/<project>/
    regression`` as the run store, builds a :class:`NixJobRunner` unless one is
    injected, and delegates to :func:`run_regression`.
    """
    run_id, ran_at = now_run_id(now)
    request = RegressionRequest(
        project_id=config.project_id,
        repo_root=Path(config.repo_root),
        reg_dir=regression_dir(Path(config.workspace_root), config.project_id),
        run_id=run_id,
        ran_at=ran_at,
        commit=config.commit,
        target_url=config.target_url,
        lanes=config.lanes,
        flaky_store_path=config.flaky_store_path,
    )
    return run_regression(request, runner or _runner_for(config))
