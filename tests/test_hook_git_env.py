"""Regression tests for the hook git-env boundary (#859).

The pre-commit hook runs this suite, and git exports repo-location variables
(GIT_INDEX_FILE, GIT_DIR, ...) to hook processes. Before the scrub in
tests/conftest.py, any test that shelled out to git in a scratch repo
inherited them and operated on the REAL repository's index instead of the
scratch one: test_dependency_review failed from the hook, and a nested
``git add -A`` once staged a 2,135-file deletion into the index of the commit
in progress. These tests pin that boundary.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import HOOK_GIT_ENV_VARS, scrub_hook_git_env

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_session_env_has_no_hook_git_vars() -> None:
    """The conftest scrub ran: no repo-location git vars survive into tests."""
    present = [v for v in HOOK_GIT_ENV_VARS if v in os.environ]
    assert present == []


def test_scrub_removes_only_hook_git_vars() -> None:
    env = {
        "GIT_DIR": "/real/.git",
        "GIT_INDEX_FILE": "/real/.git/index",
        "GIT_WORK_TREE": "/real",
        "PATH": "/usr/bin",
        "GIT_AUTHOR_NAME": "t",
    }
    scrub_hook_git_env(env)
    assert all(v not in env for v in HOOK_GIT_ENV_VARS)
    # Non-location vars are untouched.
    assert env == {"PATH": "/usr/bin", "GIT_AUTHOR_NAME": "t"}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed git argv in a test
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def test_nested_git_test_is_immune_to_hook_env(tmp_path: Path) -> None:
    """End to end: pytest launched with a hook's GIT_* env still passes a
    scratch-repo git test and leaves the outer repository's index untouched.

    Builds an "outer" repo standing in for the real repository mid-commit
    (one staged modification), then runs the exact test that used to fail
    from the hook, in a subprocess carrying the hook-style environment.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q", "-b", "main")
    _git(outer, "config", "user.email", "t@t")
    _git(outer, "config", "user.name", "t")
    (outer / "a.txt").write_text("one\n")
    (outer / "b.txt").write_text("two\n")
    _git(outer, "add", "-A")
    _git(outer, "commit", "-qm", "base")
    (outer / "a.txt").write_text("edit\n")
    _git(outer, "add", "a.txt")
    staged_before = _git(outer, "diff", "--cached", "--name-status")
    assert staged_before.strip() == "M\ta.txt"

    env = os.environ.copy()
    env["GIT_DIR"] = str(outer / ".git")
    env["GIT_INDEX_FILE"] = str(outer / ".git" / "index")
    proc = subprocess.run(
        check=False,  # asserted below so the pytest output can be shown
        args=[
            sys.executable,
            "-m",
            "pytest",
            "tests/test_dependency_review.py::test_no_manifest_change_is_skipped_without_scanning",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    staged_after = _git(outer, "diff", "--cached", "--name-status")
    assert staged_after == staged_before
    # And the outer repo still tracks its own files, not the scratch repo's.
    assert _git(outer, "ls-files").split() == ["a.txt", "b.txt"]
