"""Branch verification handoff tests for workspace ingest and its REST route."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
_WEB = Path(__file__).parent.parent / "apps" / "web-server"
for _path in (_BACKEND, _WEB):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agents.tools_pkg.tools import task_control  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from server.routes import specs  # noqa: E402
from server.services.project_workspace_service import GitOperationError  # noqa: E402

SPEC = "## Acceptance Criteria\n- The branch works"


def _git(*args):
    return subprocess.run(
        ["git", *map(str, args)], check=True, capture_output=True, text=True
    )


def _clone(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("-C", origin, "init", "-q")
    _git("-C", origin, "config", "user.email", "test@example.test")
    _git("-C", origin, "config", "user.name", "Test")
    (origin / "README.md").write_text("source")
    _git("-C", origin, "add", ".")
    _git("-C", origin, "commit", "-qm", "base")
    clone = tmp_path / "clone"
    _git("clone", "-q", origin, clone)
    return clone


def test_empty_source_tree_fails_without_scheduling(tmp_path, monkeypatch):
    clone = _clone(tmp_path)
    # A tracked tree becomes empty at the requested ref, but origin still has HEAD.
    (clone / "README.md").unlink()
    _git("-C", clone, "config", "user.email", "test@example.test")
    _git("-C", clone, "config", "user.name", "Test")
    _git("-C", clone, "add", "-u")
    _git("-C", clone, "commit", "-qm", "empty")
    _git("-C", clone, "push", "-q", "origin", "HEAD:refs/heads/empty")
    calls = []
    monkeypatch.setattr(
        "agents.planner.schedule_planner", lambda **kw: calls.append(kw)
    )
    result = task_control.create_spec_ingest_workspace(
        project_id="p",
        spec_id="s",
        spec_text=SPEC,
        root=tmp_path / "workspace",
        project_root=str(clone),
        ref="empty",
        schedule=True,
    )
    status = json.loads(
        (tmp_path / "workspace/workspaces/p/specs/s/status.json").read_text()
    )
    assert result["status"] == "failed"
    assert result["planner_scheduled"] is False
    assert status["phase"] == "source_tree_empty"
    assert status["verify"] == "not_run"
    assert status["failure_kind"] == "environment"
    assert calls == []


def test_source_provenance_and_git_env_reach_fetch(tmp_path, monkeypatch, caplog):
    clone = _clone(tmp_path)
    original_run = task_control.subprocess.run
    seen = []

    def record_run(argv, **kwargs):
        if "fetch" in argv:
            seen.append((argv, kwargs.get("env", {})))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(task_control.subprocess, "run", record_run)
    marker = "private-test-value"
    result = task_control.create_spec_ingest_workspace(
        project_id="p",
        spec_id="s",
        spec_text=SPEC,
        root=tmp_path / "workspace",
        project_root=str(clone),
        ref="HEAD",
        repo_ref="owner/name",
        pr_number=42,
        git_env={"GIT_ASKPASS": marker},
        schedule=False,
    )
    source = json.loads(
        (tmp_path / "workspace/workspaces/p/specs/s/context/source.json").read_text()
    )
    assert source["pr_number"] == 42
    assert source["source_sha"] == result["source_sha"]
    assert result["source_sha"] == _git("-C", clone, "rev-parse", "HEAD").stdout.strip()
    assert result["repo_slug"] == "owner/name"
    assert seen and all(env["GIT_ASKPASS"] == marker for _, env in seen)
    assert all(marker not in " ".join(argv) for argv, _ in seen)
    assert marker not in caplog.text


def test_bad_ref_remains_checkout_failure(tmp_path):
    clone = _clone(tmp_path)
    result = task_control.create_spec_ingest_workspace(
        project_id="p",
        spec_id="s",
        spec_text=SPEC,
        root=tmp_path / "workspace",
        project_root=str(clone),
        ref="missing-ref",
        schedule=False,
    )
    status = json.loads(
        (tmp_path / "workspace/workspaces/p/specs/s/status.json").read_text()
    )
    assert result["status"] == "failed"
    assert status["phase"] == "source_checkout_failed"


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    seen = {}
    projects = {
        "p": {
            "name": "Project",
            "path": str(tmp_path / "registered"),
            "clonedFrom": "https://github.com/owner/name.git",
        }
    }
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: projects)
    monkeypatch.setattr(specs, "_ensure_project_clone", lambda *a, **kw: None)

    async def clone_or_update(**kw):
        seen["clone"] = kw
        return tmp_path / "cloned"

    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", clone_or_update
    )

    def create(**kw):
        seen["workspace"] = kw
        return {"source_sha": "abc", "repo_slug": "owner/name"}

    monkeypatch.setattr(task_control, "create_spec_ingest_workspace", create)
    return seen, projects


@pytest.mark.asyncio
async def test_route_repo_selects_clone_and_forwards_pr(route_env, tmp_path):
    seen, _ = route_env
    result = await specs.ingest_spec(
        specs.SpecIngestRequest(
            project_id="p",
            spec_id="s",
            spec_text=SPEC,
            repo="owner/name",
            pr_number=42,
        )
    )
    assert seen["clone"]["git_url"] == "https://github.com/owner/name.git"
    assert seen["clone"]["branch"] is None
    assert seen["workspace"]["project_root"] == str((tmp_path / "cloned").resolve())
    assert seen["workspace"]["pr_number"] == 42
    assert result["tree"] == "cluster_clone"
    assert result["workspace_project_id"] == "p"
    assert result["source_sha"] == "abc"
    assert result["repo_slug"] == "owner/name"


@pytest.mark.asyncio
async def test_route_rejects_conflicting_repositories(route_env):
    seen, projects = route_env
    with pytest.raises(HTTPException) as mismatch:
        await specs.ingest_spec(
            specs.SpecIngestRequest(
                project_id="p",
                spec_id="s",
                spec_text=SPEC,
                repo="owner/name",
                git_url="https://github.com/other/name.git",
            )
        )
    assert mismatch.value.status_code == 400
    projects["p"]["clonedFrom"] = "https://github.com/other/name.git"
    with pytest.raises(HTTPException) as conflict:
        await specs.ingest_spec(
            specs.SpecIngestRequest(
                project_id="p",
                spec_id="s",
                spec_text=SPEC,
                repo="owner/name",
            )
        )
    assert conflict.value.status_code == 409
    assert "workspace" not in seen


@pytest.mark.asyncio
async def test_route_clone_failure_is_502_without_workspace(route_env, monkeypatch):
    seen, _ = route_env

    async def fail(**kw):
        raise GitOperationError("private git stderr")

    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", fail
    )
    with pytest.raises(HTTPException) as failure:
        await specs.ingest_spec(
            specs.SpecIngestRequest(
                project_id="p",
                spec_id="s",
                spec_text=SPEC,
                repo="owner/name",
            )
        )
    assert failure.value.status_code == 502
    assert "private git stderr" not in str(failure.value.detail)
    assert "workspace" not in seen


@pytest.mark.asyncio
async def test_route_token_passed_only_in_environment(route_env, monkeypatch, caplog):
    seen, _ = route_env
    token = "private-token-value"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    await specs.ingest_spec(
        specs.SpecIngestRequest(
            project_id="p",
            spec_id="s",
            spec_text=SPEC,
            repo="owner/name",
        )
    )
    assert "GIT_ASKPASS" in seen["workspace"]["git_env"]
    assert token not in seen["clone"]["git_url"]
    assert seen["clone"]["credential"] == ("x-access-token", token)
    assert token not in caplog.text


@pytest.mark.asyncio
async def test_route_unreadable_recorded_origin_is_409(route_env):
    seen, projects = route_env
    projects["p"]["clonedFrom"] = "git@gitlab.com:owner/name.git"
    with pytest.raises(HTTPException) as conflict:
        await specs.ingest_spec(
            specs.SpecIngestRequest(
                project_id="p", spec_id="s", spec_text=SPEC, repo="owner/name"
            )
        )
    assert conflict.value.status_code == 409
    assert "workspace" not in seen


@pytest.mark.asyncio
async def test_route_registered_project_without_repo_gets_no_token(
    route_env, monkeypatch
):
    seen, _ = route_env

    async def ensure(*a, **kw):
        return "/registered"

    monkeypatch.setattr(specs, "_ensure_project_clone", ensure)
    monkeypatch.setenv("GITHUB_TOKEN", "private-token-value")
    result = await specs.ingest_spec(
        specs.SpecIngestRequest(
            project_id="p",
            spec_id="s",
            spec_text=SPEC,
            git_url="https://github.com/owner/name.git",
        )
    )
    assert "GIT_ASKPASS" not in seen["workspace"]["git_env"]
    assert result["tree"] == "local"
