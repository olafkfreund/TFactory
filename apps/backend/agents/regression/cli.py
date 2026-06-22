"""Regression CLI — RFC-0018 #484 (part 5).

Makes the executor runnable end-to-end::

    python -m agents.regression run \\
        --project myapp --repo-root . \\
        --workspace ~/.tfactory/workspaces [--commit SHA] [--lanes unit,api]

Re-runs the persisted corpus on the Nix-flake-per-task k8s Job substrate
(:class:`~agents.regression.nix_runner.NixJobRunner`), diffs against the stored
baseline, writes the report, and exits non-zero when regressions are found so a
CI step or the scheduler (#488) can gate on it.

``main`` takes an optional injected ``runner`` so it is unit-testable without a
cluster; ``build_request`` / ``parse_lanes`` are factored out for the same
reason.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from .nix_runner import NixJobRunner
from .orchestrator import RegressionRequest, run_regression
from .runner import RegressionRunner
from .store import regression_dir

logger = logging.getLogger(__name__)


def now_run_id(now: datetime | None = None) -> tuple[str, str]:
    """Return ``(run_id, ran_at)`` for *now* (UTC; defaults to the real clock)."""
    now = now or datetime.now(UTC)
    return "run-" + now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_lanes(value: str | None) -> tuple[str, ...] | None:
    """Parse a ``--lanes unit,api`` value into a tuple (None when absent/empty)."""
    if not value:
        return None
    lanes = tuple(p.strip() for p in value.split(",") if p.strip())
    return lanes or None


def build_request(
    args: argparse.Namespace, *, run_id: str, ran_at: str
) -> RegressionRequest:
    """Build a :class:`RegressionRequest` from parsed CLI args."""
    return RegressionRequest(
        project_id=args.project,
        repo_root=Path(args.repo_root),
        reg_dir=regression_dir(Path(args.workspace), args.project),
        run_id=run_id,
        ran_at=ran_at,
        commit=args.commit,
        target_url=args.target_url,
        lanes=parse_lanes(args.lanes),
        flaky_store_path=Path(args.flaky_store) if args.flaky_store else None,
    )


def _default_runner(args: argparse.Namespace) -> RegressionRunner:
    spec_dir = Path(args.spec_dir) if args.spec_dir else Path(args.repo_root)
    extra_env = {"TFACTORY_TARGET_URL": args.target_url} if args.target_url else None
    return NixJobRunner(
        spec_dir=spec_dir,
        project_dir=Path(args.repo_root),
        extra_env=extra_env,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agents.regression")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser(
        "run", help="re-run the persisted corpus and diff against the baseline"
    )
    run_p.add_argument("--project", required=True, help="project id")
    run_p.add_argument(
        "--repo-root", required=True, help="checked-out project worktree"
    )
    run_p.add_argument(
        "--workspace", required=True, help="TFactory workspace root (holds runs)"
    )
    run_p.add_argument(
        "--spec-dir", help="dir carrying the contract environment (default: repo-root)"
    )
    run_p.add_argument("--commit", help="commit SHA under test (recorded)")
    run_p.add_argument("--target-url", help="deployed target URL for the tests")
    run_p.add_argument("--lanes", help="comma-separated lane filter, e.g. unit,api")
    run_p.add_argument("--flaky-store", help="path to the flaky-history store json")
    return parser


def main(
    argv: list[str] | None = None, *, runner: RegressionRunner | None = None
) -> int:
    """Entry point. Returns 1 when regressions are found, else 0."""
    args = _build_parser().parse_args(argv)
    run_id, ran_at = now_run_id()
    request = build_request(args, run_id=run_id, ran_at=ran_at)
    active_runner = runner if runner is not None else _default_runner(args)
    run, diff = run_regression(request, active_runner)
    sys.stdout.write(
        f"regression {run.run_id}: {run.totals['total']} test(s), "
        f"{len(diff.regressions)} regression(s), "
        f"{diff.counts['fixed']} fixed, {diff.counts['flaky']} flaky; "
        f"report in {request.reg_dir}\n"
    )
    return 1 if diff.has_regressions else 0
