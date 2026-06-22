"""Source-update endpoints — extracted from routes/git.py (#360 god-file split).

git.py had accumulated several unrelated routers; this carves the self-update
routes (mounted at /api/updates) into their own module. Behaviour and paths
unchanged; main.py mounts this router at the same /api/updates prefix. Shared
helpers still live in routes/git.py and are imported here.

    GET  /api/updates/source/check | source/version
    POST /api/updates/source/download
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter

from .git import run_git_command

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/source/check")
async def check_source_update():
    """Check for Magestic AI source updates."""
    return {
        "success": True,
        "data": {
            "updateAvailable": False,
            "currentVersion": "1.0.0",
            "latestVersion": "1.0.0",
        },
    }


@router.post("/source/download")
async def download_source_update():
    """
    Download Magestic AI source update via git pull.

    This endpoint updates the Magestic AI application source code by performing
    a git pull from the configured remote repository.

    Returns:
        dict: Response with success status and update details

    Raises:
        Returns error response if:
        - Not a git repository
        - Has uncommitted changes (warns user)
        - Git operations fail
        - No remote configured
    """
    try:
        # Get Magestic AI source directory (project root)
        # From git.py: __file__.parent = routes/, .parent.parent = server/,
        # .parent.parent.parent = web-server/, .parent.parent.parent.parent = apps/,
        # .parent.parent.parent.parent.parent = TFactory/ (project root)
        source_dir = Path(__file__).parent.parent.parent.parent.parent
        source_path = str(source_dir.resolve())

        # Check if it's a git repository
        git_dir = source_dir / ".git"
        if not git_dir.exists():
            return {
                "success": False,
                "error": "Magestic AI source directory is not a git repository",
            }

        # Check for uncommitted changes
        status_result = run_git_command(["status", "--porcelain"], source_path)
        if not status_result["success"]:
            return {
                "success": False,
                "error": f"Failed to check git status: {status_result.get('error', 'Unknown error')}",
            }

        has_changes = bool(status_result.get("output", "").strip())

        # Check current branch
        branch_result = run_git_command(["branch", "--show-current"], source_path)
        if not branch_result["success"]:
            return {
                "success": False,
                "error": f"Failed to get current branch: {branch_result.get('error', 'Unknown error')}",
            }

        current_branch = branch_result.get("output", "unknown").strip()

        # Check if remote exists
        remote_result = run_git_command(["remote", "-v"], source_path)
        if not remote_result["success"] or not remote_result.get("output", "").strip():
            return {
                "success": False,
                "error": "No git remote configured. Cannot update source.",
            }

        # Fetch updates from remote
        fetch_result = run_git_command(["fetch", "origin"], source_path)
        if not fetch_result["success"]:
            return {
                "success": False,
                "error": f"Failed to fetch updates: {fetch_result.get('error', 'Unknown error')}",
            }

        # Check if updates are available
        # Compare local HEAD with remote branch
        ahead_behind_result = run_git_command(
            ["rev-list", "--left-right", "--count", f"HEAD...origin/{current_branch}"],
            source_path,
        )

        updates_available = False
        commits_behind = 0

        if ahead_behind_result["success"]:
            output = ahead_behind_result.get("output", "").strip()
            if output:
                parts = output.split()
                if len(parts) >= 2:
                    commits_behind = int(parts[1])
                    updates_available = commits_behind > 0

        # If no updates available, return early
        if not updates_available:
            return {
                "success": True,
                "message": "Magestic AI is already up to date",
                "data": {
                    "updated": False,
                    "currentBranch": current_branch,
                    "hasUncommittedChanges": has_changes,
                    "commitsBehind": 0,
                },
            }

        # Warn if there are uncommitted changes
        if has_changes:
            return {
                "success": False,
                "error": "Cannot update: You have uncommitted changes. Please commit or stash them first.",
                "data": {
                    "hasUncommittedChanges": True,
                    "currentBranch": current_branch,
                    "commitsBehind": commits_behind,
                },
            }

        # Perform git pull
        pull_result = run_git_command(["pull", "origin", current_branch], source_path)
        if not pull_result["success"]:
            return {
                "success": False,
                "error": f"Failed to pull updates: {pull_result.get('error', 'Unknown error')}",
            }

        # Get updated commit info
        commit_result = run_git_command(["rev-parse", "--short", "HEAD"], source_path)
        new_commit = (
            commit_result.get("output", "unknown").strip()
            if commit_result["success"]
            else "unknown"
        )

        return {
            "success": True,
            "message": f"Magestic AI updated successfully to commit {new_commit}",
            "data": {
                "updated": True,
                "currentBranch": current_branch,
                "newCommit": new_commit,
                "commitsPulled": commits_behind,
                "pullOutput": pull_result.get("output", ""),
            },
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to update Magestic AI source: {str(e)}",
        }


@router.get("/source/version")
async def get_source_version():
    """Get current Magestic AI source version."""
    return {"success": True, "data": {"version": "1.0.0"}}
