"""
Copilot cloud agent dispatch for TFactory test writing (C2 — epic #277 / #278).

When a task carries the ``tfactory:copilot-write`` label, TFactory delegates
the actual test-writing step to the GitHub Copilot cloud agent instead of
running its own agent session.

Dispatch flow
-------------
1. Ensure a GitHub issue exists for the TFactory task (create one if absent).
2. PATCH the issue to assign ``copilot-swe-agent[bot]`` and post a prompt
   comment describing the test suite to write (lanes, frameworks, endpoints,
   AC map).
3. Poll for a Copilot-authored PR (max 59 minutes — GitHub's hard session
   limit).
4. On PR found: store ``copilot_dispatch.pr_number`` in
   ``test_task_metadata.json`` and emit ``copilot_pr_opened`` event.
5. Timeout: fall back to TFactory's own test-writing pipeline; log warning.

``test_task_metadata.json`` extension
--------------------------------------
::

    {
      "copilot_dispatch": {
        "enabled": true,
        "issue_number": 42,
        "pr_number": null,          // filled when Copilot PR is found
        "dispatched_at": "...",
        "timed_out": false
      }
    }

Environment variables
---------------------
``GITHUB_TOKEN``
    Required.  Used for both ``gh`` CLI calls and the REST API.
    If absent a ``CopilotDispatchError`` is raised immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# GitHub's hard session limit for Copilot cloud agents
_COPILOT_TIMEOUT_SECONDS: int = 59 * 60  # 59 minutes
# Polling cadence while waiting for a Copilot PR
_POLL_INTERVAL_SECONDS: int = 30

_COPILOT_BOT_LOGIN_PREFIX: str = "copilot-swe-agent"
_COPILOT_BOT_TYPE: str = "Bot"

_TFACTORY_LABEL: str = "tfactory:copilot-write"


class CopilotDispatchError(RuntimeError):
    """Raised when dispatch cannot proceed (missing token, API error, etc.)."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def dispatch_test_writing(
    *,
    spec_dir: Path,
    repo_full_name: str,
    task_description: str,
    lanes: list[str],
    frameworks: dict[str, str],
    ac_map: dict[str, list[str]],
    issue_number: int | None = None,
) -> int | None:
    """Dispatch test writing to the Copilot cloud agent.

    Assigns Copilot to a GitHub issue with a detailed prompt, then polls for
    up to 59 minutes.  On success returns the PR number; on timeout returns
    ``None`` (caller should fall back to TFactory's own writer).

    Args:
        spec_dir: Path to the workspace spec directory (holds
            ``test_task_metadata.json``).
        repo_full_name: ``owner/repo`` string.
        task_description: Human-readable description of what to test.
        lanes: Lane names to generate (e.g. ``["unit", "api"]``).
        frameworks: Lane → framework mapping (e.g. ``{"unit": "pytest"}``).
        ac_map: AC-id → source-file list mapping.
        issue_number: Existing GitHub issue number.  If ``None`` a new issue
            is created automatically.

    Returns:
        PR number (int) on success, or ``None`` on timeout/failure.

    Raises:
        CopilotDispatchError: If ``GITHUB_TOKEN`` is missing or a fatal API
            error occurs before the poll begins.
    """
    _require_github_token()

    if issue_number is None:
        issue_number = _ensure_issue(repo_full_name, task_description)

    _assign_copilot(repo_full_name, issue_number, lanes, frameworks, ac_map)
    _write_dispatch_metadata(
        spec_dir, issue_number, dispatched=True, pr_number=None, timed_out=False
    )

    logger.info(
        "copilot_dispatch: assigned copilot-swe-agent to issue #%d in %s",
        issue_number,
        repo_full_name,
    )

    pr_number = await _poll_for_copilot_pr(repo_full_name, issue_number)

    if pr_number is not None:
        _write_dispatch_metadata(
            spec_dir,
            issue_number,
            dispatched=True,
            pr_number=pr_number,
            timed_out=False,
        )
        logger.info(
            "copilot_dispatch: Copilot PR #%d found for issue #%d",
            pr_number,
            issue_number,
        )
        return pr_number

    # Timed out — record and signal fallback
    _write_dispatch_metadata(
        spec_dir, issue_number, dispatched=True, pr_number=None, timed_out=True
    )
    logger.warning(
        "copilot_dispatch: timed out after %d minutes waiting for Copilot PR "
        "(issue #%d). Falling back to TFactory's own test writer.",
        _COPILOT_TIMEOUT_SECONDS // 60,
        issue_number,
    )
    return None


# ---------------------------------------------------------------------------
# GitHub issue helpers
# ---------------------------------------------------------------------------


def _require_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise CopilotDispatchError(
            "GITHUB_TOKEN is required for Copilot dispatch but is not set. "
            "Export GITHUB_TOKEN before using the tfactory:copilot-write label."
        )
    return token


def _ensure_issue(repo_full_name: str, title: str) -> int:
    """Create a GitHub issue for the TFactory task and return its number."""
    result = subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repo_full_name,
            "--title",
            title,
            "--body",
            f"TFactory test-writing task dispatched to Copilot.\n\nLabel: `{_TFACTORY_LABEL}`",
            "--label",
            _TFACTORY_LABEL,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CopilotDispatchError(
            f"Failed to create GitHub issue in {repo_full_name}: {result.stderr.strip()}"
        )
    # gh returns the issue URL; extract number from the last path segment
    url = result.stdout.strip()
    try:
        return int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError as exc:
        raise CopilotDispatchError(
            f"Could not parse issue number from gh output: {url!r}"
        ) from exc


def _assign_copilot(
    repo_full_name: str,
    issue_number: int,
    lanes: list[str],
    frameworks: dict[str, str],
    ac_map: dict[str, list[str]],
) -> None:
    """Assign copilot-swe-agent and post a detailed test-suite prompt comment."""
    # Build a rich prompt comment so Copilot knows exactly what to write
    lanes_str = ", ".join(lanes) if lanes else "unit"
    fw_lines = "\n".join(f"  - {lane}: {fw}" for lane, fw in frameworks.items())
    ac_lines = (
        "\n".join(f"  - {ac_id}: {', '.join(files)}" for ac_id, files in ac_map.items())
        or "  (no AC map provided)"
    )

    prompt_body = (
        f"@copilot Please write the TFactory test suite for this task.\n\n"
        f"**Lanes:** {lanes_str}\n\n"
        f"**Frameworks:**\n{fw_lines}\n\n"
        f"**Acceptance-criteria → source-file map:**\n{ac_lines}\n\n"
        "Use the TFactory MCP server tools (`tfactory_get_test_plan`, "
        "`tfactory_get_spec`, `tfactory_get_ac_map`) to get full context "
        "before writing any test file."
    )

    # Post the prompt as a comment (Copilot picks it up from @copilot mentions)
    subprocess.run(
        [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            repo_full_name,
            "--body",
            prompt_body,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    # Assign copilot-swe-agent[bot] via the REST API
    subprocess.run(
        [
            "gh",
            "api",
            f"/repos/{repo_full_name}/issues/{issue_number}",
            "-X",
            "PATCH",
            "-f",
            "assignees[]=copilot-swe-agent",
        ],
        capture_output=True,
        text=True,
        # Non-fatal if assignee already set or bot not installed — we already
        # posted the @copilot mention which is the primary trigger.
    )


# ---------------------------------------------------------------------------
# PR polling
# ---------------------------------------------------------------------------


async def find_copilot_pr(repo_full_name: str, issue_number: int) -> int | None:
    """Return the PR number of a Copilot-authored PR referencing *issue_number*.

    Returns ``None`` if no such PR exists yet.

    Note: GitHub API returns ``user.login = "copilot-swe-agent"`` (no ``[bot]``
    suffix in the login field) and ``user.type = "Bot"``.
    """
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "gh",
            "api",
            f"/repos/{repo_full_name}/pulls",
            "--jq",
            (
                f"[.[] | select("
                f'.user.type == "Bot" and '
                f'(.user.login | startswith("{_COPILOT_BOT_LOGIN_PREFIX}")) and '
                f'(.body // "" | contains("#{issue_number}")))'
                f"] | first | .number"
            ),
        ],
        capture_output=True,
        text=True,
    )
    number = result.stdout.strip()
    return int(number) if number and number != "null" else None


async def _poll_for_copilot_pr(repo_full_name: str, issue_number: int) -> int | None:
    """Poll until a Copilot PR appears or the 59-minute timeout expires."""
    deadline = asyncio.get_event_loop().time() + _COPILOT_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        pr = await find_copilot_pr(repo_full_name, issue_number)
        if pr is not None:
            return pr
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    return None


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _write_dispatch_metadata(
    spec_dir: Path,
    issue_number: int,
    *,
    dispatched: bool,
    pr_number: int | None,
    timed_out: bool,
) -> None:
    """Persist copilot_dispatch block into ``test_task_metadata.json``."""
    meta_path = spec_dir / "test_task_metadata.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass

    meta["copilot_dispatch"] = {
        "enabled": dispatched,
        "issue_number": issue_number,
        "pr_number": pr_number,
        "dispatched_at": datetime.now(timezone.utc).isoformat(),
        "timed_out": timed_out,
    }
    meta_path.write_text(json.dumps(meta, indent=2))


def read_dispatch_metadata(spec_dir: Path) -> dict[str, Any] | None:
    """Return the ``copilot_dispatch`` block from metadata, or ``None``."""
    meta_path = spec_dir / "test_task_metadata.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text())
        return data.get("copilot_dispatch")
    except (json.JSONDecodeError, OSError):
        return None


__all__ = [
    "dispatch_test_writing",
    "find_copilot_pr",
    "read_dispatch_metadata",
    "CopilotDispatchError",
]
