"""Every .husky hook must be executable in the index, or git skips it (#921).

`.husky/commit-msg` was committed 100644. Git refuses to run a non-executable
hook and says so only as a `hint:` on stderr, which is not a failure and is
easy to miss:

    hint: The '.../.husky/commit-msg' hook was ignored because it's not set as
    executable.

So the hook had never run in any clone since it was added, while `.husky/pre-commit`
(100755) ran on every commit -- meaning "the hooks passed" and "git skipped a
hook entirely" looked identical.

The mode lives in the index, so this is a property of the repo rather than of
one checkout, and a test can assert it. Same class as AIFactory#1087.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _husky_entries() -> list[tuple[str, str]]:
    """Return (mode, path) for every tracked file under .husky/."""
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", ".husky/"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    entries = []
    for line in out.stdout.splitlines():
        if not line:
            continue
        meta, path = line.split("\t", 1)
        entries.append((meta.split()[0], path))
    return entries


def test_every_husky_hook_is_executable_in_the_index() -> None:
    entries = _husky_entries()
    assert entries, "expected tracked files under .husky/"

    # Only hook scripts need +x; a README or config under .husky/ does not.
    not_executable = [
        path
        for mode, path in entries
        if not Path(path).name.endswith((".md", ".json", ".yml", ".yaml"))
        and mode != "100755"
    ]
    assert not not_executable, (
        "git silently SKIPS a hook that is not executable, printing only a "
        f"'hint:' -- so these have never run: {not_executable}. "
        "Fix with: git update-index --chmod=+x <path>"
    )


def test_commit_msg_accepts_the_conventions_this_repo_actually_uses() -> None:
    """The type list must cover real history, or enabling the hook blocks releases.

    `release:` is used on every dev->main promotion (27 of the last 400 commits)
    and was absent from the pattern. A hook that never runs drifts out of step
    with the convention it exists to enforce, and only bites when switched on.
    """
    hook = _REPO / ".husky" / "commit-msg"
    pattern_line = next(
        line
        for line in hook.read_text(encoding="utf-8").splitlines()
        if line.startswith("pattern=")
    )
    for required in ("release", "security", "feat", "fix", "chore", "revert"):
        assert f"{required}|" in pattern_line or f"|{required})" in pattern_line, (
            f"commit-msg rejects '{required}:', which this repo's history uses"
        )
