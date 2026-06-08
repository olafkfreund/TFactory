#!/usr/bin/env python3
"""
Tests for epic #277 — GitHub Agentic Integration (TFactory side).

Covers:
  C1 — GitHub Models provider routing (phase_config + factory)
  C2 — Copilot dispatch module (copilot_dispatch.py)
  C3 — MCP Copilot HTTP endpoint (/mcp route)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make apps/backend and apps/web-server importable
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
_WEB_SERVER_DIR = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

# ---------------------------------------------------------------------------
# C1 — GitHub Models provider routing
# ---------------------------------------------------------------------------


def test_infer_provider_github_models():
    """github-models/ prefix routes to openai-compatible."""
    from phase_config import infer_provider_from_model

    assert infer_provider_from_model("github-models/openai/gpt-4.1") == "openai-compatible"
    assert infer_provider_from_model("github-models/openai/gpt-4o") == "openai-compatible"
    assert infer_provider_from_model("GITHUB-MODELS/openai/gpt-4.1") == "openai-compatible"


def test_strip_provider_prefix_github_models():
    """github-models/ prefix is stripped to the bare catalog path."""
    from phase_config import strip_provider_prefix

    assert strip_provider_prefix("github-models/openai/gpt-4.1") == "openai/gpt-4.1"
    assert strip_provider_prefix("github-models/mistral-ai/mistral-large") == "mistral-ai/mistral-large"


def test_infer_provider_github_models_does_not_affect_other_prefixes():
    """Existing prefix routing is not disturbed by the github-models addition."""
    from phase_config import infer_provider_from_model

    assert infer_provider_from_model("copilot:claude-sonnet-4.5") == "copilot"
    assert infer_provider_from_model("studio:gemini-2.5-flash") == "openai-compatible"
    assert infer_provider_from_model("claude-sonnet-4-5") == "claude"


def test_get_provider_extra_kwargs_github_models(monkeypatch):
    """github-models/ resolves correct base_url, model, and api_key from GITHUB_TOKEN."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token_abc123")
    from phase_config import get_provider_extra_kwargs

    extra = get_provider_extra_kwargs("openai-compatible", "github-models/openai/gpt-4.1")
    assert extra["base_url"] == "https://models.github.ai/inference"
    assert extra["api_key"] == "ghp_test_token_abc123"
    assert extra["model"] == "openai/gpt-4.1"


def test_get_provider_extra_kwargs_github_models_uses_default_model(monkeypatch):
    """GITHUB_MODELS_DEFAULT env var sets the fallback model."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_token")
    monkeypatch.setenv("GITHUB_MODELS_DEFAULT", "openai/gpt-4o")
    from importlib import reload
    import phase_config
    reload(phase_config)
    extra = phase_config.get_provider_extra_kwargs("openai-compatible", "github-models/")
    assert extra["model"] == "openai/gpt-4o"


def test_get_provider_extra_kwargs_github_models_missing_token(monkeypatch):
    """Missing GITHUB_TOKEN raises ValueError with a clear message."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from phase_config import get_provider_extra_kwargs

    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        get_provider_extra_kwargs("openai-compatible", "github-models/openai/gpt-4.1")


def test_factory_alias_github_models():
    """github-models alias in factory maps to openai-compatible."""
    from providers.factory import _PROVIDER_ALIASES

    assert _PROVIDER_ALIASES.get("github-models") == "openai-compatible"
    assert _PROVIDER_ALIASES.get("github-models-inference") == "openai-compatible"


# ---------------------------------------------------------------------------
# C2 — Copilot dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copilot_dispatch_missing_token(monkeypatch, tmp_path):
    """dispatch_test_writing raises CopilotDispatchError when GITHUB_TOKEN absent."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from agents.copilot_dispatch import CopilotDispatchError, dispatch_test_writing

    with pytest.raises(CopilotDispatchError, match="GITHUB_TOKEN"):
        await dispatch_test_writing(
            spec_dir=tmp_path,
            repo_full_name="owner/repo",
            task_description="Add login tests",
            lanes=["unit"],
            frameworks={"unit": "pytest"},
            ac_map={},
            issue_number=1,
        )


@pytest.mark.asyncio
async def test_find_copilot_pr_returns_none_when_absent():
    """find_copilot_pr returns None when gh returns 'null'."""
    from agents.copilot_dispatch import find_copilot_pr

    mock_result = MagicMock()
    mock_result.stdout = "null\n"
    mock_result.returncode = 0

    with patch("agents.copilot_dispatch.subprocess.run", return_value=mock_result):
        result = await find_copilot_pr("owner/repo", 42)
    assert result is None


@pytest.mark.asyncio
async def test_find_copilot_pr_returns_number_when_found():
    """find_copilot_pr returns the PR number when gh returns a number."""
    from agents.copilot_dispatch import find_copilot_pr

    mock_result = MagicMock()
    mock_result.stdout = "99\n"
    mock_result.returncode = 0

    with patch("agents.copilot_dispatch.subprocess.run", return_value=mock_result):
        result = await find_copilot_pr("owner/repo", 42)
    assert result == 99


def test_write_dispatch_metadata_creates_file(tmp_path):
    """_write_dispatch_metadata writes correct JSON structure."""
    from agents.copilot_dispatch import _write_dispatch_metadata, read_dispatch_metadata

    _write_dispatch_metadata(tmp_path, 7, dispatched=True, pr_number=None, timed_out=False)

    meta = read_dispatch_metadata(tmp_path)
    assert meta is not None
    assert meta["enabled"] is True
    assert meta["issue_number"] == 7
    assert meta["pr_number"] is None
    assert meta["timed_out"] is False


def test_write_dispatch_metadata_merges_existing(tmp_path):
    """_write_dispatch_metadata preserves other keys in test_task_metadata.json."""
    meta_path = tmp_path / "test_task_metadata.json"
    meta_path.write_text(json.dumps({"existing_key": "keep_me"}))

    from agents.copilot_dispatch import _write_dispatch_metadata

    _write_dispatch_metadata(tmp_path, 3, dispatched=True, pr_number=55, timed_out=False)

    final = json.loads(meta_path.read_text())
    assert final["existing_key"] == "keep_me"
    assert final["copilot_dispatch"]["pr_number"] == 55


# ---------------------------------------------------------------------------
# C3 — MCP Copilot HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_client(tmp_path, monkeypatch):
    """Return a TestClient for the mcp_copilot router with a tmp workspace."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import importlib

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("COPILOT_MCP_TFACTORY_TOKEN", raising=False)

    # Force fresh import so _workspace_root() picks up the monkeypatched env
    sys.modules.pop("server.routes.mcp_copilot", None)
    import server.routes.mcp_copilot as mod  # noqa: E402

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app)


def _post_mcp(client, method: str, params: dict | None = None) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        body["params"] = params
    resp = client.post("/mcp", json=body)
    return resp.json()


def test_mcp_initialize(mcp_client):
    data = _post_mcp(mcp_client, "initialize")
    assert "result" in data
    assert data["result"]["serverInfo"]["name"] == "tfactory"


def test_mcp_tools_list_returns_six_tools(mcp_client):
    data = _post_mcp(mcp_client, "tools/list")
    tools = data["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "tfactory_get_test_plan",
        "tfactory_get_ac_map",
        "tfactory_get_coverage",
        "tfactory_get_results",
        "tfactory_get_spec",
        "tfactory_report_result",
    }


def test_mcp_unknown_task_returns_error(mcp_client):
    data = _post_mcp(
        mcp_client,
        "tools/call",
        {"name": "tfactory_get_test_plan", "arguments": {"task_id": "nonexistent-task"}},
    )
    content = json.loads(data["result"]["content"][0]["text"])
    assert content["error"] == "task not found"
    assert data["result"]["isError"] is True


def test_mcp_coverage_returns_null_gracefully(mcp_client, tmp_path):
    """tfactory_get_coverage returns coverage_pct: null when no report exists yet."""
    # Seed a minimal workspace
    spec_dir = tmp_path / "workspaces" / "proj" / "specs" / "spec-abc"
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text('{"status": "triaged"}')

    data = _post_mcp(
        mcp_client,
        "tools/call",
        {"name": "tfactory_get_coverage", "arguments": {"task_id": "spec-abc", "lane": "unit"}},
    )
    content = json.loads(data["result"]["content"][0]["text"])
    assert content["coverage_pct"] is None
    assert "error" not in content


def test_mcp_report_result_writes_metadata(mcp_client, tmp_path):
    """tfactory_report_result persists results and returns accepted: true."""
    spec_dir = tmp_path / "workspaces" / "proj" / "specs" / "spec-xyz"
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text('{"status": "triaged"}')

    data = _post_mcp(
        mcp_client,
        "tools/call",
        {
            "name": "tfactory_report_result",
            "arguments": {
                "task_id": "spec-xyz",
                "lane": "unit",
                "passed": 10,
                "failed": 0,
                "coverage_pct": 90.5,
                "summary": "All unit tests pass",
            },
        },
    )
    content = json.loads(data["result"]["content"][0]["text"])
    assert content["accepted"] is True

    meta = json.loads((spec_dir / "test_task_metadata.json").read_text())
    assert meta["copilot_reported_results"]["unit"]["passed"] == 10


def test_mcp_unknown_tool_returns_error(mcp_client):
    data = _post_mcp(
        mcp_client,
        "tools/call",
        {"name": "nonexistent_tool", "arguments": {}},
    )
    assert "error" in data
    assert data["error"]["code"] == -32601


def test_mcp_unknown_method_returns_error(mcp_client):
    data = _post_mcp(mcp_client, "ping")
    assert "error" in data
    assert data["error"]["code"] == -32601


def _make_mcp_client(monkeypatch, tmp_path, *, token: str | None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    if token:
        monkeypatch.setenv("COPILOT_MCP_TFACTORY_TOKEN", token)
    else:
        monkeypatch.delenv("COPILOT_MCP_TFACTORY_TOKEN", raising=False)

    sys.modules.pop("server.routes.mcp_copilot", None)
    import server.routes.mcp_copilot as mod  # noqa: E402

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app, raise_server_exceptions=False)


def test_mcp_bearer_auth_rejects_wrong_token(monkeypatch, tmp_path):
    """Wrong Bearer token returns 401."""
    client = _make_mcp_client(monkeypatch, tmp_path, token="secret-token")
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


def test_mcp_bearer_auth_accepts_correct_token(monkeypatch, tmp_path):
    """Correct Bearer token passes auth."""
    client = _make_mcp_client(monkeypatch, tmp_path, token="correct-token")
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer correct-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "tfactory"
