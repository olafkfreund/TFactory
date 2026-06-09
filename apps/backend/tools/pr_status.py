"""PR status-check helper — WS1 of the enterprise 90-day plan.

Publishes a GitHub **commit status** reflecting the quality-gate verdict
(``agents/quality_gate.py``), so a PR shows a red/green "TFactory / tests"
check that can gate merge. Sister to ``tools/pr_comment.py`` and built to the
same principles:

  - **Dry-run first.** Default ``dry_run=True`` returns the argv without
    invoking subprocess. Safe for tests + CI.
  - **Injected runner_fn.** Same shape as ``pr_comment``/``git_writer``.
  - **gh CLI, explicit repo + sha.** Commit statuses are keyed on a SHA, so
    both ``repo_slug`` and ``sha`` are required (unlike pr_comment, gh can't
    infer them for the statuses API).

Command shape::

  gh api -X POST repos/<owner>/<repo>/statuses/<sha> \
      -f state=success|failure -f context="TFactory / tests" \
      -f description="<≤140 chars>" [-f target_url=<url>]

A commit status (not the Checks API) is used deliberately: it needs only a
token with ``repo:status`` scope — no GitHub App — which is the lowest-friction
path for self-hosted installs. The Checks API is a future enhancement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

# GitHub commit-status states.
_VALID_STATES = frozenset({"error", "failure", "pending", "success"})
_MAX_DESCRIPTION = 140  # GitHub truncates beyond this


class PRStatusError(Exception):
    """Raised for fatal pr_status failures."""


class _SubprocessResultLike(Protocol):
    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


# ─── Data shapes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PRStatusRequest:
    """One commit-status operation.

    Args:
        repo_dir: cwd for the gh invocation.
        repo_slug: ``owner/repo`` — required (the statuses API is repo-keyed).
        sha: The commit SHA to attach the status to — required.
        state: One of error/failure/pending/success.
        context: The status-check label shown on the PR.
        description: Short summary (truncated to 140 chars by GitHub).
        target_url: Optional link (e.g. to the triage report).
    """

    repo_dir: Path
    repo_slug: str
    sha: str
    state: str
    context: str = "TFactory / tests"
    description: str = ""
    target_url: str = ""


@dataclass(frozen=True)
class PRStatusResult:
    """Outcome of a post_pr_status call."""

    ok: bool = True
    dry_run: bool = True
    argv: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""


# ─── Subprocess seam ────────────────────────────────────────────────────


def _default_runner_fn(
    argv: list[str],
    *,
    cwd: Path,
    stdin: str | None = None,
) -> _SubprocessResultLike:
    """Default runner_fn that ACTUALLY shells out."""
    import subprocess

    return subprocess.run(
        argv,
        cwd=str(cwd),
        input=stdin if stdin is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


# ─── Argv assembly ──────────────────────────────────────────────────────


def _build_argv(request: PRStatusRequest) -> tuple[str, ...]:
    """Assemble the ``gh api`` argv. Pure function — same request → same argv."""
    endpoint = f"repos/{request.repo_slug}/statuses/{request.sha}"
    argv: list[str] = [
        "gh",
        "api",
        "-X",
        "POST",
        endpoint,
        "-f",
        f"state={request.state}",
    ]
    argv += ["-f", f"context={request.context}"]
    if request.description:
        argv += ["-f", f"description={request.description[:_MAX_DESCRIPTION]}"]
    if request.target_url:
        argv += ["-f", f"target_url={request.target_url}"]
    return tuple(argv)


# ─── Public entrypoint ──────────────────────────────────────────────────


def post_pr_status(
    request: PRStatusRequest,
    *,
    dry_run: bool = True,
    runner_fn: Callable[..., _SubprocessResultLike] | None = None,
) -> PRStatusResult:
    """Publish the commit status via ``gh api``.

    Dry-run returns the argv without invoking subprocess. Validation:
      - Invalid ``state`` → ok=False, no invocation.
      - Missing ``repo_slug`` or ``sha`` → ok=False, no invocation.
    """
    runner = runner_fn or _default_runner_fn

    if request.state not in _VALID_STATES:
        return PRStatusResult(
            ok=False,
            dry_run=dry_run,
            error=f"invalid state {request.state!r} (must be one of {sorted(_VALID_STATES)})",
        )
    if not request.repo_slug or not request.sha:
        return PRStatusResult(
            ok=False,
            dry_run=dry_run,
            error="repo_slug and sha are both required for a commit status",
        )

    argv = _build_argv(request)

    if dry_run:
        return PRStatusResult(ok=True, dry_run=True, argv=argv)

    if not request.repo_dir.exists():
        return PRStatusResult(
            ok=False,
            dry_run=False,
            argv=argv,
            error=f"repo_dir does not exist: {request.repo_dir}",
        )

    res = runner(list(argv), cwd=request.repo_dir, stdin=None)
    if res.returncode != 0:
        return PRStatusResult(
            ok=False,
            dry_run=False,
            argv=argv,
            error=f"gh api statuses failed: {(res.stderr or '').strip()[:300]}",
        )
    return PRStatusResult(ok=True, dry_run=False, argv=argv)
