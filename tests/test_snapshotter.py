"""Tests for the AIFactory→TFactory spec snapshotter — Task 3 (#4).

Sub-task 3.1 snapshotter side:
  - happy path copies spec.md + implementation_plan.json
  - copies are read-only (0o444)
  - source.json contains expected metadata
  - missing source spec dir → SnapshotError
  - missing spec.md → result.has_spec_md = False + warning, no exception
  - missing implementation_plan.json → result.has_plan_json = False + warning
  - project_root_path=None → no diff captured, warning recorded
  - real git repo → diff captured, sha recorded
  - bad git repo → soft fail with warning, no exception
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from workspaces import SnapshotError, snapshot_aifactory_spec


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def aifactory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated AIFactory root for one test."""
    root = tmp_path / "aifactory"
    monkeypatch.setenv("TFACTORY_AIFACTORY_ROOT", str(root))
    return root


@pytest.fixture
def populated_spec(aifactory: Path) -> Path:
    """An AIFactory spec dir with spec.md + implementation_plan.json."""
    spec = aifactory / "workspaces" / "demo" / "specs" / "001-login"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("# Login spec\n\nAccept on bcrypt success.\n")
    (spec / "implementation_plan.json").write_text(
        '{"phases": [{"id": "p1", "name": "auth"}]}'
    )
    return spec


# ── Happy path ───────────────────────────────────────────────────────────


def test_snapshot_happy_path(populated_spec: Path, tmp_path: Path) -> None:
    dest = tmp_path / "tfactory" / "workspaces" / "demo" / "specs" / "001-login"
    res = snapshot_aifactory_spec(
        project_id="demo",
        spec_id="001-login",
        branch="feature/login",
        base_ref="main",
        project_root_path=None,  # skip git
        dest_spec_dir=dest,
    )
    assert res.has_spec_md is True
    assert res.has_plan_json is True
    assert res.aifactory_spec_dir == str(populated_spec)
    assert (dest / "context" / "aifactory_spec.md").exists()
    assert (dest / "context" / "aifactory_plan.json").exists()
    assert (dest / "context" / "source.json").exists()


def test_copies_are_read_only(populated_spec: Path, tmp_path: Path) -> None:
    dest = tmp_path / "tfactory" / "workspaces" / "demo" / "specs" / "001-login"
    snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    for fname in ("aifactory_spec.md", "aifactory_plan.json"):
        mode = (dest / "context" / fname).stat().st_mode & 0o777
        assert mode == 0o444, f"{fname} mode is {oct(mode)}, expected 0o444"


def test_source_json_schema(populated_spec: Path, tmp_path: Path) -> None:
    dest = tmp_path / "tfactory" / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    data = json.loads((dest / "context" / "source.json").read_text())
    expected_keys = {
        "project_id", "spec_id", "branch", "base_ref",
        "aifactory_spec_dir", "snapshotted_at",
        "has_spec_md", "has_plan_json", "has_diff_patch",
        "sha_at_handover", "diff_stat", "warnings",
    }
    assert expected_keys.issubset(data.keys())
    assert data["project_id"] == "demo"
    assert data["spec_id"] == "001-login"
    assert data["branch"] == "feature/x"
    assert data["base_ref"] == "main"


# ── Soft-fail paths (warnings, not exceptions) ───────────────────────────


def test_missing_spec_md_is_soft_fail(aifactory: Path, tmp_path: Path) -> None:
    """An AIFactory dir with only the JSON file is still snapshottable."""
    spec = aifactory / "workspaces" / "demo" / "specs" / "001"
    spec.mkdir(parents=True)
    (spec / "implementation_plan.json").write_text('{"phases": []}')
    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001",
        branch="f/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    assert res.has_spec_md is False
    assert res.has_plan_json is True
    assert any("spec.md missing" in w for w in res.warnings)


def test_missing_plan_json_is_soft_fail(aifactory: Path, tmp_path: Path) -> None:
    spec = aifactory / "workspaces" / "demo" / "specs" / "001"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("# spec")
    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001",
        branch="f/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    assert res.has_spec_md is True
    assert res.has_plan_json is False
    assert any("implementation_plan.json missing" in w for w in res.warnings)


def test_project_root_none_skips_git_with_warning(populated_spec: Path, tmp_path: Path) -> None:
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=None, dest_spec_dir=tmp_path / "ws",
    )
    assert res.has_diff_patch is False
    assert any("project_root_path not provided" in w for w in res.warnings)


def test_bad_project_root_is_soft_fail(populated_spec: Path, tmp_path: Path) -> None:
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=tmp_path / "does" / "not" / "exist",
        dest_spec_dir=tmp_path / "ws",
    )
    assert res.has_diff_patch is False
    assert any("not a directory" in w for w in res.warnings)


# ── Hard fail: missing source spec dir ───────────────────────────────────


def test_missing_source_spec_dir_raises(aifactory: Path, tmp_path: Path) -> None:
    with pytest.raises(SnapshotError) as exc:
        snapshot_aifactory_spec(
            project_id="ghost", spec_id="404",
            branch="f/x", base_ref="main",
            project_root_path=None, dest_spec_dir=tmp_path / "ws",
        )
    assert "AIFactory spec dir not found" in str(exc.value)


# ── Real-git happy path ──────────────────────────────────────────────────


def _git(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *cmd],
        capture_output=True, text=True, check=True
    ).stdout.strip()


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_git_diff_captured_for_real_repo(populated_spec: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "tester"], repo)

    (repo / "a.py").write_text("def add(a, b):\n    return a + b\n")
    _git(["add", "a.py"], repo)
    _git(["commit", "-m", "base"], repo)

    _git(["checkout", "-b", "feature/login"], repo)
    (repo / "a.py").write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    _git(["add", "a.py"], repo)
    _git(["commit", "-m", "add sub"], repo)

    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=repo, dest_spec_dir=dest,
    )

    assert res.has_diff_patch is True
    diff_text = (dest / "context" / "diff.patch").read_text()
    assert "def sub" in diff_text  # the added function
    assert res.sha_at_handover is not None
    assert len(res.sha_at_handover) == 40  # full sha
    assert res.diff_stat is not None
