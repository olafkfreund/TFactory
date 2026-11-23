"""MCP tool routing between local workspaces and cluster endpoints."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.sdk_stub import SDK_INSTALLED  # noqa: E402

if isinstance(sys.modules.get("claude_agent_sdk"), MagicMock):
    sys.modules.pop("claude_agent_sdk", None)
    sys.modules.pop("claude_agent_sdk.types", None)
    sys.modules.pop("agents.tools_pkg.tools.task_control", None)

from agents.tools_pkg.tools import task_control  # noqa: E402


def _body(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("TFACTORY_API_URL", raising=False)
    monkeypatch.delenv("TFACTORY_PORTAL_URL", raising=False)
    if not SDK_INSTALLED:
        # The module's handlers need the decorator only to capture the closures.
        def decorator(name, _description, _schema):
            def wrap(handler):
                handler.name = name
                handler.handler = handler
                return handler

            return wrap

        monkeypatch.setattr(task_control, "tool", decorator)
        monkeypatch.setattr(task_control, "SDK_TOOLS_AVAILABLE", True)
    return {
        tool.name: tool.handler for tool in task_control.create_task_control_tools()
    }


@pytest.fixture
def cluster(tools, monkeypatch):
    monkeypatch.setenv("TFACTORY_API_URL", "https://tfactory.example/api-root")
    calls = []

    async def api(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/projects":
            return [{"id": "p1"}]
        if path == "/api/spec-tasks":
            return {
                "tasks": [
                    {
                        "project_id": "p1",
                        "spec_id": "same",
                        "task_id": "same",
                        "status": "done",
                    },
                    {
                        "project_id": "p2",
                        "spec_id": "same",
                        "task_id": "same",
                        "status": "done",
                    },
                ]
            }
        if path.startswith("/api/spec-tasks/"):
            project = path.split("/")[3]
            return {
                "project_id": project,
                "status_json": {"task_id": "same", "status": "done"},
                "source": {"source_sha": project},
                "report": {"format": "md", "body": project},
            }
        if path == "/api/specs/ingest":
            return {
                "task_id": kwargs["json"]["spec_id"],
                "workspace_project_id": "p1",
                "tree": "cluster_clone",
            }
        raise AssertionError(path)

    monkeypatch.setattr(task_control, "_api", api)
    return tools, calls


@pytest.mark.asyncio
async def test_create_from_spec_posts_branch_repo_pr_and_portal(cluster):
    tools, calls = cluster
    args = {
        "project_id": "p1",
        "spec_id": "s",
        "spec_text": "## Acceptance Criteria\n- x",
        "repo": "owner/name",
        "branch": "build",
        "pr_number": 8,
    }
    preview = _body(await tools["task_create_from_spec"](args))
    assert preview["preview"] is True
    assert calls == []
    result = _body(await tools["task_create_from_spec"]({**args, "confirm": True}))
    assert calls == [
        (
            "POST",
            "/api/specs/ingest",
            {
                "json": {
                    "project_id": "p1",
                    "spec_id": "s",
                    "spec_text": args["spec_text"],
                    "ref": "build",
                    "repo": "owner/name",
                    "pr_number": 8,
                },
                "timeout": 660.0,
            },
        )
    ]
    assert result["tree"] == "cluster_clone"
    assert result["portal_url"] == "https://tfactory.example/api-root/tasks/s"


@pytest.mark.asyncio
async def test_create_and_run_preview_mapping_and_portal_override(cluster, monkeypatch):
    tools, calls = cluster
    args = {
        "project_id": "p1",
        "spec_id": "s",
        "branch": "build",
        "base_ref": "main",
        "spec_text": "## Acceptance Criteria\n- x",
        "repo": "owner/name",
        "pr_number": 8,
    }
    assert _body(await tools["task_create_and_run"](args))["preview"] is True
    assert calls == []
    monkeypatch.setenv("TFACTORY_PORTAL_URL", "https://portal.example/")
    result = _body(await tools["task_create_and_run"]({**args, "confirm": True}))
    assert calls[-1][0:2] == ("POST", "/api/specs/ingest")
    assert calls[-1][2]["json"]["source_branch"] == "build"
    assert calls[-1][2]["json"]["pr_number"] == 8
    assert calls[-1][2]["timeout"] == 660.0
    assert result["portal_url"] == "https://portal.example/tasks/s"
    error = await tools["task_create_and_run"](
        {**args, "spec_text": "", "confirm": True}
    )
    assert error["isError"] is True
    assert "spec_text" in error["content"][0]["text"]


@pytest.mark.asyncio
async def test_invalid_creation_args_make_no_post(cluster, monkeypatch):
    tools, calls = cluster
    args = {
        "project_id": "p1",
        "spec_id": "s",
        "spec_text": "spec",
        "repo": "owner/name",
    }
    error = await tools["task_create_from_spec"](
        {**args, "branch": "one", "ref": "two", "confirm": True}
    )
    assert error["isError"] is True
    assert calls == []
    monkeypatch.delenv("TFACTORY_API_URL")
    error = await tools["task_create_from_spec"]({**args, "confirm": True})
    assert "TFACTORY_API_URL" in error["content"][0]["text"]
    assert calls == []


@pytest.mark.asyncio
async def test_cluster_reads_listing_ambiguity_and_rerun(cluster):
    tools, calls = cluster
    assert _body(await tools["project_list"]({}))["projects"] == [{"id": "p1"}]
    assert _body(await tools["task_list"]({}))["count"] == 2
    assert ("GET", "/api/projects", {}) in calls
    assert any(
        method == "GET" and path == "/api/spec-tasks" for method, path, _ in calls
    )
    for name in ("task_status", "report_get"):
        error = await tools[name]({"task_id": "same"})
        assert error["isError"] is True
        assert "p1" in error["content"][0]["text"]
        assert "p2" in error["content"][0]["text"]
    status = _body(await tools["task_status"]({"task_id": "same", "project_id": "p2"}))
    report = _body(await tools["report_get"]({"task_id": "same", "project_id": "p1"}))
    assert status["source"]["source_sha"] == "p2"
    assert report["body"] == "p1"
    assert ("GET", "/api/spec-tasks/p2/same", {}) in calls
    assert ("GET", "/api/spec-tasks/p1/same", {"params": {"report": "md"}}) in calls
    assert (await tools["task_rerun"]({"task_id": "same"}))["isError"] is True


@pytest.mark.asyncio
async def test_local_duplicate_ids_require_project_and_single_hit_still_works(
    tools, tmp_path
):
    (tmp_path / "projects.json").write_text(
        json.dumps({"p1": {"path": str(tmp_path)}, "p2": {"path": str(tmp_path)}})
    )

    def write(project):
        path = tmp_path / "workspaces" / project / "specs" / "same"
        (path / "findings").mkdir(parents=True)
        (path / "status.json").write_text(
            json.dumps({"task_id": "same", "project_id": project, "status": "done"})
        )
        (path / "findings/triage_report.md").write_text(project)

    write("p1")
    write("p2")
    for name in ("task_status", "report_get"):
        error = await tools[name]({"task_id": "same"})
        assert error["isError"] is True
        assert "p1" in error["content"][0]["text"]
        assert "p2" in error["content"][0]["text"]
    selected = _body(
        await tools["task_status"]({"task_id": "same", "project_id": "p2"})
    )
    assert selected["project_id"] == "p2"
    assert selected["tree"] == "local"
    assert (
        _body(await tools["report_get"]({"task_id": "same", "project_id": "p2"}))[
            "body"
        ]
        == "p2"
    )
    (tmp_path / "workspaces/p1/specs/same/status.json").unlink()
    (tmp_path / "workspaces/p1/specs/same/findings/triage_report.md").unlink()
    (tmp_path / "workspaces/p1/specs/same/findings").rmdir()
    (tmp_path / "workspaces/p1/specs/same").rmdir()
    assert _body(await tools["task_status"]({"task_id": "same"}))["project_id"] == "p2"
