#!/usr/bin/env python3
"""Tests for the linked-worktree gitdir repair (TFactory #868).

A linked git worktree stores an ABSOLUTE path to its gitdir. The spec tree is
created as one on the control plane under ``/home/nonroot/.tfactory/...``; the
verify Job mounts the SAME PVC at ``/work``, so that absolute path does not
exist inside the Job and every git command against the tree dies with
``fatal: not a git repository: (null)`` -- which is why ``git_writer`` could
never commit the generated tests back.

These tests build a REAL git repo plus a linked worktree, then relocate the
whole tree to a different absolute path (the control-plane -> ``/work`` remount
in miniature) and assert the failure before the repair and real file CONTENTS
read through git after it. Both on-disk layouts are covered:

* the repo is a SIBLING of the worktree's workspace -- the real TFactory layout
  (``workspaces/<uuid>/specs/<spec>/.worktree`` next to
  ``workspaces/<project-name>``), where the repo is NOT an ancestor;
* the repo is an ANCESTOR of the worktree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agents import verify_pipeline as vp
from agents.utils import repair_linked_worktree

_TRACKED = "print('the file contents under test')\n"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _git("init", "-q", ".", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    (path / "app.py").write_text(_TRACKED)
    _git("add", "app.py", cwd=path)
    _git("commit", "-qm", "init", cwd=path)


def _relocated(tmp_path: Path, *, sibling: bool) -> Path:
    """Build repo + linked worktree, then MOVE the tree. Returns the new path.

    ``sibling=True`` reproduces the real TFactory layout: the base clone is
    ``workspaces/<project-name>`` and the spec worktree lives under a DIFFERENT
    workspace directory, so no ancestor of the worktree is a git repo.
    """
    src = tmp_path / "control-plane"
    repo = src / "workspaces" / "hello-python"
    _init_repo(repo)

    if sibling:
        wt = src / "workspaces" / "216efca1-uuid" / "specs" / "038-spec" / ".worktree"
    else:
        wt = repo / "specs" / "038-spec" / ".worktree"
    wt.parent.mkdir(parents=True, exist_ok=True)
    add = _git("worktree", "add", "--detach", "--force", str(wt), "HEAD", cwd=repo)
    assert add.returncode == 0, add.stderr
    assert (wt / ".git").read_text().startswith(f"gitdir: {src}")

    dst = tmp_path / "work"
    shutil.move(str(src), str(dst))
    return dst / wt.relative_to(src)


def _assert_broken(wt: Path) -> None:
    broken = _git("rev-parse", "HEAD", cwd=wt)
    assert broken.returncode != 0
    assert "not a git repository" in broken.stderr


def _assert_repaired(wt: Path) -> None:
    fixed = _git("rev-parse", "HEAD", cwd=wt)
    assert fixed.returncode == 0, fixed.stderr
    # The file-contents proof: read a tracked blob back THROUGH the repository.
    show = _git("show", "HEAD:app.py", cwd=wt)
    assert show.returncode == 0, show.stderr
    assert show.stdout == _TRACKED


def test_repairs_sibling_layout(tmp_path):
    """The real layout: the base clone is a sibling, not an ancestor (#868)."""
    wt = _relocated(tmp_path, sibling=True)
    # No ancestor of the worktree is a git repo -- an ancestor-only walk fails.
    assert not any((p / ".git").is_dir() for p in wt.parents)

    _assert_broken(wt)
    repair_linked_worktree(wt)
    _assert_repaired(wt)

    # Idempotent: a second repair is a no-op, not a failure.
    repair_linked_worktree(wt)
    _assert_repaired(wt)


def test_repairs_ancestor_layout(tmp_path):
    wt = _relocated(tmp_path, sibling=False)
    _assert_broken(wt)
    repair_linked_worktree(wt)
    _assert_repaired(wt)


def test_main_repairs_the_worktree_before_the_pipeline_runs(monkeypatch, tmp_path):
    """The Job entrypoint must repair before any stage touches git."""
    wt = _relocated(tmp_path, sibling=True)
    seen: dict[str, subprocess.CompletedProcess] = {}

    async def fake_pipeline(spec_dir, project_dir, *, mode="initial"):
        # Stands in for git_writer / dependency_review / planner / agents.utils:
        # they all just run git against project_dir.
        seen["git"] = _git("rev-parse", "HEAD", cwd=project_dir)
        return True, "triaged"

    monkeypatch.setattr(vp, "run_verify_pipeline", fake_pipeline)
    assert vp.main(["--spec", str(wt.parent), "--project", str(wt)]) == 0
    assert seen["git"].returncode == 0, seen["git"].stderr


def test_repair_is_a_noop_outside_a_broken_linked_worktree(tmp_path):
    """A plain dir, a normal clone, and a healthy worktree are left alone."""
    repair_linked_worktree(tmp_path)  # no .git at all

    clone = tmp_path / "clone"
    _init_repo(clone)
    assert (clone / ".git").is_dir()
    repair_linked_worktree(clone)
    assert (clone / ".git").is_dir()

    healthy = clone / "specs" / "wt"
    healthy.parent.mkdir(parents=True)
    assert (
        _git(
            "worktree", "add", "--detach", "-f", str(healthy), "HEAD", cwd=clone
        ).returncode
        == 0
    )
    before = (healthy / ".git").read_text()
    repair_linked_worktree(healthy)
    assert (healthy / ".git").read_text() == before
