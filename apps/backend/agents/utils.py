"""
Utility Functions for Agent System
===================================

Helper functions for git operations, plan management, and file syncing.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_latest_commit(project_dir: Path) -> str | None:
    """Get the hash of the latest git commit."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def get_commit_count(project_dir: Path) -> int:
    """Get the total number of commits."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0


def load_test_plan(spec_dir: Path) -> dict | None:
    """Load the implementation plan JSON."""
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        return None
    try:
        with open(plan_file) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def find_subtask_in_plan(plan: dict, subtask_id: str) -> dict | None:
    """Find a subtask by ID in the plan."""
    for phase in plan.get("phases", []):
        for subtask in phase.get("subtasks", []):
            if subtask.get("id") == subtask_id:
                return subtask
    return None


def find_phase_for_subtask(plan: dict, subtask_id: str) -> dict | None:
    """Find the phase containing a subtask."""
    for phase in plan.get("phases", []):
        for subtask in phase.get("subtasks", []):
            if subtask.get("id") == subtask_id:
                return phase
    return None


def sync_plan_to_source(spec_dir: Path, source_spec_dir: Path | None) -> bool:
    """
    Sync test_plan.json from worktree back to source spec directory.

    When running in isolated mode (worktrees), the agent updates the implementation
    plan inside the worktree. This function syncs those changes back to the main
    project's spec directory so the frontend/UI can see the progress.

    Args:
        spec_dir: Current spec directory (may be inside worktree)
        source_spec_dir: Original spec directory in main project (outside worktree)

    Returns:
        True if sync was performed, False if not needed or failed
    """
    # Skip if no source specified or same path (not in worktree mode)
    if not source_spec_dir:
        return False

    # Resolve paths and check if they're different
    spec_dir_resolved = spec_dir.resolve()
    source_spec_dir_resolved = source_spec_dir.resolve()

    if spec_dir_resolved == source_spec_dir_resolved:
        return False  # Same directory, no sync needed

    # Sync the implementation plan
    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        return False

    source_plan_file = source_spec_dir / "test_plan.json"

    try:
        shutil.copy2(plan_file, source_plan_file)
        logger.debug(f"Synced implementation plan to source: {source_plan_file}")
        return True
    except Exception as e:
        logger.warning(f"Failed to sync implementation plan to source: {e}")
        return False


def _find_main_repo(project_dir: Path, stale_gitdir: Path) -> Path | None:
    """Locate the main repo that owns the linked worktree at ``project_dir``.

    ``stale_gitdir`` is the (now wrong) ``<repo>/.git/worktrees/<id>`` path read
    out of the worktree's ``.git`` file; the repo's directory NAME survives the
    remount even though its absolute path does not. Two layouts are searched, for
    each ancestor of the worktree, deepest first:

    * the ancestor itself is the repo (a worktree nested inside its own clone);
    * the repo is a SIBLING named like the old one -- which is the real TFactory
      layout: the spec tree lives at ``workspaces/<uuid>/specs/<spec>/.worktree``
      while its base clone is ``workspaces/<project-name>``, so the repo is NOT
      an ancestor and an ancestor-only walk finds nothing.

    ponytail: re-roots on the repo's own directory name only. If a future layout
    moves the clone deeper than one level under a shared ancestor, widen this to
    try longer suffixes of ``stale_gitdir``.
    """
    parts = stale_gitdir.parts
    if ".git" not in parts:
        return None
    repo_name = parts[len(parts) - 1 - parts[::-1].index(".git") - 1]
    for ancestor in project_dir.parents:
        for candidate in (ancestor, ancestor / repo_name):
            if (candidate / ".git").is_dir():
                return candidate
    return None


def repair_linked_worktree(project_dir: Path) -> None:
    """Re-point a relocated linked git worktree at its real gitdir (#868).

    The spec tree is a *linked* worktree created on the control plane
    (``task_control._add_spec_worktree``), so its ``.git`` is a FILE holding an
    ABSOLUTE ``gitdir:`` path under ``/home/nonroot/.tfactory/...``. The verify
    Job mounts the same PVC at ``/work``, so that absolute path does not exist
    there and every git command against the tree dies with
    ``fatal: not a git repository: (null)`` -- which is why ``git_writer`` could
    never commit the generated tests back while the verdict still looked healthy.

    ``git worktree repair`` is exactly this repair and is idempotent, but it must
    run FROM THE MAIN REPO: git cannot discover the repository from the broken
    linked tree. Repairing from there rewrites BOTH pointer files -- the main
    repo's ``.git/worktrees/<id>/gitdir`` and the linked tree's ``.git``.

    Symmetric by construction: it always repairs toward the view of the process
    that calls it, so the Job repairs to ``/work`` and a later in-pod stage
    repairs back to the control-plane root.

    No-op unless ``project_dir`` really is a linked worktree whose pointer is
    broken. Best-effort: a failure is logged and the caller continues, because a
    pointer repair must never be why a verify reports a different verdict.
    """
    dot_git = project_dir / ".git"
    if not dot_git.is_file():
        return  # a normal clone, or not a repo at all -- nothing to repair
    try:
        pointer = dot_git.read_text().strip()
    except OSError:
        return
    if not pointer.startswith("gitdir:"):
        return
    stale = Path(pointer.split(":", 1)[1].strip())
    if (stale / "gitdir").is_file():
        return  # the pointer already resolves from here

    main_repo = _find_main_repo(project_dir, stale)
    if main_repo is None:
        logger.warning(
            "[worktree-repair] %s is a linked worktree pointing at a missing "
            "gitdir %s, and no main repo was found near it; git commands against "
            "it will fail (#868)",
            project_dir,
            stale,
        )
        return
    try:
        proc = subprocess.run(
            ["git", "-C", str(main_repo), "worktree", "repair", str(project_dir)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        logger.warning("[worktree-repair] git worktree repair failed: %s (#868)", exc)
        return
    if proc.returncode != 0:
        logger.warning(
            "[worktree-repair] repair of %s from %s failed: %s (#868)",
            project_dir,
            main_repo,
            (proc.stderr or proc.stdout).strip()[:200],
        )
        return
    logger.info(
        "[worktree-repair] repaired linked worktree %s against %s (#868)",
        project_dir,
        main_repo,
    )
