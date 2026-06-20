"""Build runner — run `.tfactory.yml` `build:` steps before the lanes (#233).

Produces the artifact under test (e.g. ``docker build`` an image a
``docker_run`` target then runs, or ``npm run build``). Steps run in declared
order; the first non-zero exit stops the run and is reported. ``runner_fn`` is
injectable so tests never shell out.

Pairs with ``DockerRunTarget`` + ``DockerRunRuntime``: a typical config builds
``myapp:test`` here, then ``docker_run`` runs it for the browser/api lane.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BuildStepResult:
    ok: bool
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class BuildResult:
    ok: bool
    steps: list[BuildStepResult] = field(default_factory=list)
    error: str = ""


def _argv_for_step(step, repo_root: Path) -> tuple[list[str], Path]:
    """Return ``(argv, cwd)`` for a build step. Raises ValueError on a bad step."""
    if step.type == "docker":
        ctx = step.context or "."
        argv = [
            "docker",
            "build",
            "-t",
            step.image,
            "-f",
            str(repo_root / (step.dockerfile or "Dockerfile")),
            str(repo_root / ctx),
        ]
        return argv, repo_root
    if step.type == "command":
        cwd = repo_root / step.cwd if step.cwd else repo_root
        # Run through a shell so "npm ci && npm run build" works as written.
        return ["sh", "-c", step.command], cwd
    raise ValueError(f"unknown build step type: {step.type!r}")


def run_build_steps(
    steps,
    *,
    repo_root: Path,
    runner_fn: Callable | None = None,
) -> BuildResult:
    """Run each build step in order; stop on the first failure.

    Returns a ``BuildResult`` (ok + per-step results). Never raises on a build
    failure — the caller decides whether a failed build should skip the lane.
    """
    runner = runner_fn or subprocess.run
    result = BuildResult(ok=True)
    for step in steps or []:
        try:
            argv, cwd = _argv_for_step(step, repo_root)
        except ValueError as exc:
            result.ok = False
            result.error = str(exc)
            return result
        cp = runner(argv, cwd=str(cwd), capture_output=True, text=True)
        sr = BuildStepResult(
            ok=cp.returncode == 0,
            argv=tuple(argv),
            returncode=cp.returncode,
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
        )
        result.steps.append(sr)
        if not sr.ok:
            result.ok = False
            result.error = (
                f"build step failed (exit {cp.returncode}): "
                f"{' '.join(argv)} — {(cp.stderr or '').strip()[:200]}"
            )
            return result
    return result
