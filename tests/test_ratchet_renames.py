#!/usr/bin/env python3
"""A moved file must be gated, and measured against where it MOVED FROM.

TFactory#1005 + AIFactory#1218. These are two halves of one defect and the
fixes pull in opposite directions, which is why both belong in one file:

* ``--diff-filter=AM`` misses renames entirely. ``diff.renames`` has defaulted
  to true since git 2.9, so a moved file has status **R** and was gated by
  NOTHING — a 1561-line ``git mv`` sailed through, and so would a move that
  carried new violations in.
* Fixing that alone creates the opposite bug: looked up on base by its HEAD
  path, a moved file is not found, its baseline reads 0, and every pre-existing
  violation reports as net-new. AIFactory's fork did exactly that and made a
  pure ``git mv`` report ``0 -> 167`` — a gate punishing the cleanup it exists
  to encourage.

NOTE: this fork takes ``packages: list[str]`` where the hub takes a comma
string. Ported by hand for that reason — the files are forks, not copies,
and a blind copy of the hub test silently passed a string that iterated as
characters and matched nothing.

Stdlib only and no parametrize, matching the sibling ratchet suites: the
code-quality job installs ruff and mypy and nothing else, so a pytest decorator
would be an untyped import under ``mypy --strict``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ratchet_lint

LEGACY = "import os\n\n\ndef f():\n    print(os.getcwd())\n"


def _git(repo: Path, *args: str) -> str:
    # Constant git argv plus test-owned paths, and git from PATH — exactly what
    # ratchet_lint._run does. The two codes anchor to DIFFERENT lines: S603 to
    # the call, S607 to the argv list. Putting both on one line leaves the other
    # unsuppressed AND the noqa unused (RUF100). Measured, not guessed.
    res = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def _new_repo() -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    """An empty git repo with a scripts/ dir, ready for a first commit."""
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "scripts").mkdir()
    return repo, tmp


def _repo_with_a_move() -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    """A repo whose HEAD renamed scripts/old.py -> scripts/new.py, unchanged."""
    repo, tmp = _new_repo()
    (repo / "scripts" / "old.py").write_text(LEGACY)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "mv", "scripts/old.py", "scripts/new.py")
    _git(repo, "commit", "-qm", "move it")
    return repo, tmp


def test_a_renamed_file_is_gated_at_all() -> None:
    """The TFactory#1005 half: ACMR sees the move that AM dropped."""
    repo, tmp = _repo_with_a_move()
    try:
        cwd = Path.cwd()
        os.chdir(repo)
        try:
            ratchet_lint.rename_sources.cache_clear()
            changed = ratchet_lint.changed_python_files("HEAD~1", ["scripts"])
        finally:
            os.chdir(cwd)
        assert changed == ["scripts/new.py"], changed
    finally:
        tmp.cleanup()


def test_the_baseline_follows_the_file_to_its_old_path() -> None:
    """The AIFactory#1218 half: a pure move must measure base == head.

    Without this, ``git show HEAD~1:scripts/new.py`` fails, the baseline is read
    as empty, and the move reports every pre-existing violation as net-new.
    """
    repo, tmp = _repo_with_a_move()
    try:
        cwd = Path.cwd()
        os.chdir(repo)
        try:
            ratchet_lint.rename_sources.cache_clear()
            base_src = ratchet_lint.file_at_base("HEAD~1", "scripts/new.py")
        finally:
            os.chdir(cwd)
        assert base_src == LEGACY, (
            f"the moved file's baseline must come from its pre-rename path; got {base_src!r}"
        )
    finally:
        tmp.cleanup()


def test_a_genuinely_new_file_still_reads_an_empty_baseline() -> None:
    """The guard against over-correcting: a NEW file has no base, and must not
    inherit one from some unrelated rename pair."""
    repo, tmp = _new_repo()
    try:
        (repo / "scripts" / "keep.py").write_text("x = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        (repo / "scripts" / "brand_new.py").write_text(LEGACY)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "add a new file")

        cwd = Path.cwd()
        os.chdir(repo)
        try:
            ratchet_lint.rename_sources.cache_clear()
            base_src = ratchet_lint.file_at_base("HEAD~1", "scripts/brand_new.py")
            changed = ratchet_lint.changed_python_files("HEAD~1", ["scripts"])
        finally:
            os.chdir(cwd)
        assert base_src is None, base_src
        assert changed == ["scripts/brand_new.py"], changed
    finally:
        tmp.cleanup()


def test_rename_sources_is_empty_when_nothing_moved() -> None:
    repo, tmp = _new_repo()
    try:
        (repo / "scripts" / "a.py").write_text("x = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        (repo / "scripts" / "a.py").write_text("x = 2\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "edit")

        cwd = Path.cwd()
        os.chdir(repo)
        try:
            ratchet_lint.rename_sources.cache_clear()
            pairs = ratchet_lint.rename_sources("HEAD~1")
        finally:
            os.chdir(cwd)
        assert dict(pairs) == {}, pairs
    finally:
        tmp.cleanup()


def test_the_cached_rename_map_cannot_be_mutated_by_a_caller() -> None:
    """The map is cached, so every caller gets the SAME object back.

    A plain dict there could be modified by one caller and silently observed by
    the next — a cache poisoned in-process. MappingProxyType makes that a
    TypeError instead of a subtle wrong baseline.
    """
    repo, tmp = _repo_with_a_move()
    try:
        cwd = Path.cwd()
        os.chdir(repo)
        try:
            ratchet_lint.rename_sources.cache_clear()
            pairs = ratchet_lint.rename_sources("HEAD~1")
        finally:
            os.chdir(cwd)
        assert pairs["scripts/new.py"] == "scripts/old.py"
        try:
            pairs["scripts/new.py"] = "hijacked"  # type: ignore[index]
        except TypeError:
            pass
        else:
            raise AssertionError("the cached rename map is mutable")
    finally:
        tmp.cleanup()


def test_an_unreadable_rename_lookup_says_so_instead_of_degrading_quietly(
    capsys,
) -> None:
    """A gate that gets quietly less accurate is the failure mode being fixed.

    On a git error the map falls back to empty — which silently measures a moved
    file against no baseline. It must at least SAY so on stderr.
    """
    repo, tmp = _repo_with_a_move()
    try:
        cwd = Path.cwd()
        os.chdir(repo)
        try:
            ratchet_lint.rename_sources.cache_clear()
            pairs = ratchet_lint.rename_sources("no-such-ref-anywhere")
        finally:
            os.chdir(cwd)
        assert dict(pairs) == {}
        err = capsys.readouterr().err
        assert "rename information" in err
        # git's OWN message must be forwarded too. Without this the
        # `sys.stderr.write(res.stderr)` line can be deleted and the test still
        # passes — asserting on the ref name rather than git's phrasing keeps it
        # stable across git versions and locales.
        assert "no-such-ref-anywhere" in err
    finally:
        tmp.cleanup()
