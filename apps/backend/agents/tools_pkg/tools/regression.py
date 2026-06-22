"""Regression MCP tool — RFC-0018 #512 (follow-up to #488).

``regression_run`` — re-run a project's persisted test corpus on the
Nix-flake-per-task k8s Job substrate and diff against the stored baseline, over
the MCP control plane. Wraps the shared ``agents.regression.run_for_project``
trigger that the CLI, HTTP endpoint, and nightly CronJob also use.

Runs synchronously (in a worker thread) so the caller gets the verdict back —
the unattended/scaled path remains the nightly regression CronJob (#488 part 2)
and the non-blocking surface is the HTTP endpoint (#488 part 3).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

try:
    from claude_agent_sdk import tool

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    tool = None  # type: ignore[assignment]

from agents.regression import ProjectScheduleConfig, run_for_project

from .task_control import _workspace_root


def _format_json(data: Any) -> dict[str, Any]:
    """MCP text-content envelope, matching the task-control tools."""
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def create_regression_tools() -> list[Any]:
    """Create the regression MCP tools (``regression_run``).

    Returns ``[]`` when the Claude Agent SDK isn't available, mirroring
    ``create_task_control_tools``.
    """
    if not SDK_TOOLS_AVAILABLE:
        return []

    tools: list[Any] = []

    @tool(
        "regression_run",
        "Re-run a project's persisted test corpus on the Nix-Job substrate and "
        "diff against the stored baseline. Returns the run_id, totals, and which "
        "tests regressed. Runs synchronously (may take minutes); the unattended "
        "path is the nightly regression CronJob.",
        {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project id; its worktree lives under the workspaces root",
                },
                "commit": {
                    "type": "string",
                    "description": "Optional commit SHA under test (recorded on the run)",
                },
            },
            "required": ["project_id"],
        },
    )
    async def regression_run(args: dict[str, Any]) -> dict[str, Any]:
        project_id = args["project_id"]
        workspaces = _workspace_root() / "workspaces"
        config = ProjectScheduleConfig(
            project_id=project_id,
            repo_root=workspaces / project_id,
            workspace_root=workspaces,
            commit=args.get("commit"),
        )
        run, diff = await asyncio.to_thread(run_for_project, config)
        return _format_json(
            {
                "run_id": run.run_id,
                "has_regressions": diff.has_regressions,
                "regressions": list(diff.regressions),
                "totals": run.totals,
            }
        )

    tools.append(regression_run)
    return tools
