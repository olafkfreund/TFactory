"""Per-commit coverage lookup + record for CI (#851, #861).

``.github/workflows/pr-review-tests.yml`` has been calling ``/api/coverage``
since epic #277 and the endpoint was never written -- every request 404'd, the
workflow resolved ``PCT=N/A``, and (until #848) hung a commit status at
``pending`` forever. This is the missing server half.

``POST`` is the other half of the same hole (#861): the GET was built to serve a
figure and, for months, no producer existed, so it truthfully answered "nothing
recorded" for every commit ever asked about. The regression orchestrator records
a point only when a run produces a project coverage figure, and TFactory's own
repo has no regression corpus -- so the CI job that measures coverage for the
gate records it here, and the GET finally has something to return.

Keyed on COMMIT, not on pull request. ``coverage_trend.json`` already records a
``commit`` per regression run, and the calling workflow already knows
``github.event.pull_request.head.sha``. Resolving a PR number to a SHA here
would mean giving TFactory a GitHub credential and a network round-trip to learn
something the caller already had. ``pr`` is still accepted, for the message only.

**Absent coverage is a 200 with ``coverage_pct: null`` and a reason, not a 404.**
A 404 is what "this endpoint does not exist" looks like, and conflating the two
is precisely what let a missing endpoint masquerade as missing data for months.
The caller can now tell "TFactory has no coverage for this commit yet" from
"TFactory has no such endpoint".
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

# apps/backend carries the regression store helpers.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from agents.regression.coverage_trend import (  # noqa: E402
    CoveragePoint,
    coverage_trend_path,
    load_trend,
    record_coverage,
)
from agents.regression.store import regression_dir  # noqa: E402

router = APIRouter()

# A git remote may be https://host/owner/name(.git) or git@host:owner/name(.git).
# Only the trailing owner/name matters for matching a `repo` query.
_GIT_REMOTE_TIMEOUT_S = 5
# A repo reference is exactly owner + name; anything longer is a nested group
# path (GitLab) whose trailing two segments identify the project.
_REPO_PATH_PARTS = 2


def _workspace_root() -> Path:
    env_val = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(env_val).expanduser() if env_val else Path.home() / ".tfactory"


def _normalise_repo(value: str) -> str:
    """Reduce a remote URL or `owner/name` to a comparable `owner/name`."""
    text = value.strip().removesuffix(".git")
    if text.startswith(("http://", "https://")):
        text = text.split("://", 1)[1]
        text = text.split("/", 1)[1] if "/" in text else text
    elif "@" in text and ":" in text:  # git@host:owner/name
        text = text.split(":", 1)[1]
    parts = [p for p in text.split("/") if p]
    if len(parts) >= _REPO_PATH_PARTS:
        return "/".join(parts[-_REPO_PATH_PARTS:]).lower()
    return text.lower()


def _remote_of(path: Path) -> str:
    """`origin` remote of a checkout, or "" when it is not a git tree.

    Read from disk rather than trusting the workspace directory name. The name
    is a slug (`owner-repo`), which is ambiguous: owner `x` / repo `a-b` and
    owner `x-a` / repo `b` both slug to `x-a-b`.
    """
    try:
        res = subprocess.run(  # noqa: S603 - fixed argv, path from our own store
            ["git", "-C", str(path), "remote", "get-url", "origin"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_GIT_REMOTE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _projects_for_repo(repo: str) -> list[str]:
    """Project ids whose checkout points at *repo*."""
    wanted = _normalise_repo(repo)
    root = _workspace_root() / "workspaces"
    if not root.is_dir():
        return []
    return [
        d.name
        for d in sorted(root.iterdir())
        if d.is_dir() and _normalise_repo(_remote_of(d)) == wanted
    ]


def _coverage_for_commit(project_id: str, sha: str) -> dict[str, Any] | None:
    reg_dir = regression_dir(_workspace_root(), project_id)
    trend_file = coverage_trend_path(reg_dir)
    if not trend_file.is_file():
        return None
    for point in reversed(load_trend(trend_file)):
        # Prefix match: the trend stores whatever the run recorded, which may be
        # abbreviated, while a caller passes the full head SHA.
        if point.commit and (
            sha.startswith(point.commit) or point.commit.startswith(sha)
        ):
            return {
                "coverage_pct": point.coverage_pct,
                "commit": point.commit,
                "run_id": point.run_id,
                "ran_at": point.ran_at,
            }
    return None


@router.get("/api/coverage")
async def get_coverage(
    repo: str = Query(..., description="owner/name of the repository"),
    sha: str = Query("", description="commit to report coverage for"),
    pr: int | None = Query(None, description="PR number, used only in messages"),
    project_id: str = Query(
        "", description="skip repo resolution and use this project"
    ),
) -> dict[str, Any]:
    """Coverage for one commit, or an explicit reason there is none.

    Always 200. See the module docstring: a 404 here would be indistinguishable
    from the endpoint being absent, which is the bug this closes.
    """
    label = f"PR #{pr}" if pr else sha[:8] or "request"

    if not sha:
        return {
            "coverage_pct": None,
            "reason": f"no sha supplied for {label}; coverage is keyed on commit",
        }

    candidates = [project_id] if project_id else _projects_for_repo(repo)
    if not candidates:
        return {
            "coverage_pct": None,
            "reason": f"no TFactory project has a checkout of {repo}",
        }

    for pid in candidates:
        found = _coverage_for_commit(pid, sha)
        if found:
            return {**found, "project_id": pid, "repo": repo}

    return {
        "coverage_pct": None,
        "reason": (
            f"no regression run has recorded coverage for {sha[:8]} "
            f"({label}); checked project(s): {', '.join(candidates)}"
        ),
    }


class CoverageReport(BaseModel):
    """A measured project-coverage figure for one commit."""

    repo: str = Field(min_length=3, description="owner/name of the repository")
    # A commit, not free text: this value is matched against stored commits and
    # echoed back into messages, so constrain it to what a SHA can be.
    sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$", description="commit measured")
    coverage_pct: float = Field(ge=0.0, le=100.0, description="line coverage percent")
    run_id: str | None = Field(
        default=None, max_length=120, description="producer of the figure"
    )
    ran_at: str | None = Field(
        default=None, max_length=64, description="ISO-8601 measurement time"
    )


@router.post("/api/coverage", status_code=status.HTTP_201_CREATED)
async def record_commit_coverage(report: CoverageReport) -> dict[str, Any]:
    """Record a coverage figure against a commit, so the GET above can serve it.

    Unlike the GET, an unresolvable repo here is a **404**, not a 200 with a
    reason. The GET's null-plus-reason exists so a caller can tell "no data" from
    "no endpoint"; a write has no such ambiguity to preserve, and accepting a
    figure into nowhere would be the silent fallback (Factory#431) all over
    again -- the producer would look like it recorded something it did not.
    """
    candidates = _projects_for_repo(report.repo)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no TFactory project has a checkout of {report.repo}",
        )
    project_id = candidates[0]
    point = CoveragePoint(
        # Provenance stays readable in the ledger: a CI measurement and a
        # regression run are both project coverage, but they are not the same
        # producer and a trend reader should be able to tell them apart.
        run_id=report.run_id or f"ci-{report.sha[:8]}",
        ran_at=report.ran_at or datetime.now(UTC).isoformat(timespec="seconds"),
        coverage_pct=report.coverage_pct,
        commit=report.sha,
    )
    reg_dir = regression_dir(_workspace_root(), project_id)
    record_coverage(coverage_trend_path(reg_dir), point)
    return {
        "recorded": True,
        "project_id": project_id,
        "commit": report.sha,
        "coverage_pct": report.coverage_pct,
        "run_id": point.run_id,
    }
