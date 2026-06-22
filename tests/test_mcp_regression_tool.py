"""Tests for the regression MCP tool — RFC-0018 #512 (follow-up to #488)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# conftest.py pre-mocks claude_agent_sdk for offline collection; this tool needs
# the real ``@tool`` decorator. Drop the mock so the module re-imports against
# the actual SDK (mirrors tests/test_tfactory_mcp_tools.py).
if isinstance(sys.modules.get("claude_agent_sdk"), MagicMock):
    sys.modules.pop("claude_agent_sdk", None)
    sys.modules.pop("claude_agent_sdk.types", None)
    sys.modules.pop("agents.tools_pkg.tools.regression", None)
    sys.modules.pop("agents.tools_pkg.tools.task_control", None)

try:
    import claude_agent_sdk  # noqa: F401 — availability probe
except ImportError:
    pytest.skip("claude_agent_sdk not installed", allow_module_level=True)

import agents.tools_pkg.tools.regression as mod  # noqa: E402
from agents.regression import (  # noqa: E402
    RegressionRun,
    TestOutcome,
    TestStatus,
    diff_runs,
)


def _handlers():
    return {t.name: t.handler for t in mod.create_regression_tools()}


def test_tool_is_registered():
    assert "regression_run" in _handlers()


async def test_regression_run_returns_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    captured = {}

    def fake_run_for_project(config, **_kw):
        captured["config"] = config
        cur = RegressionRun(
            run_id="run-x",
            project_id=config.project_id,
            ran_at="2026-06-22T12:00:00Z",
            results=(TestOutcome("a", "unit", "pytest", TestStatus.FAILED),),
            commit=config.commit,
        )
        base = RegressionRun(
            run_id="base",
            project_id=config.project_id,
            ran_at="2026-06-22T00:00:00Z",
            results=(TestOutcome("a", "unit", "pytest", TestStatus.PASSED),),
        )
        return cur, diff_runs(cur, base)

    monkeypatch.setattr(mod, "run_for_project", fake_run_for_project)

    result = await _handlers()["regression_run"](
        {"project_id": "demo", "commit": "abc"}
    )
    payload = json.loads(result["content"][0]["text"])

    assert payload["run_id"] == "run-x"
    assert payload["has_regressions"] is True
    assert payload["regressions"] == ["a"]
    assert payload["totals"]["failed"] == 1
    # workspace-root + project worktree resolved under <root>/workspaces/<project>
    assert captured["config"].repo_root == tmp_path / "workspaces" / "demo"
    assert captured["config"].workspace_root == tmp_path / "workspaces"
    assert captured["config"].commit == "abc"
