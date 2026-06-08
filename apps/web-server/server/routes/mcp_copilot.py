"""
TFactory MCP HTTP endpoint for the Copilot cloud agent (C3 — epic #277 / #279).

Exposes a minimal POST-only MCP HTTP transport at ``/mcp`` so the Copilot cloud
agent can call TFactory tools directly from its MCP config.

Transport
---------
- ``POST /mcp`` — handles ``initialize``, ``tools/list``, ``tools/call``
- Auth: ``Authorization: Bearer <COPILOT_MCP_TFACTORY_TOKEN>``

The full Streamable HTTP spec (GET channel, ``Mcp-Session-Id``) is out of
scope for v1; Copilot's cloud agent works fine with POST-only.

Register in repo Settings → Copilot → MCP servers::

    {
      "tfactory": {
        "type": "http",
        "url": "https://tfactory.example.com/mcp",
        "headers": { "Authorization": "Bearer ${COPILOT_MCP_TFACTORY_TOKEN}" }
      }
    }

Tools exposed
-------------
``tfactory_get_test_plan``   — ``test_plan.json`` for a task
``tfactory_get_ac_map``      — AC-id → source-file mapping
``tfactory_get_coverage``    — latest coverage summary for a lane
``tfactory_get_results``     — most recent test-run results for a lane
``tfactory_get_spec``        — spec.md + acceptance criteria
``tfactory_report_result``   — write-back: Copilot reports a run result

Environment variables
---------------------
``COPILOT_MCP_TFACTORY_TOKEN``
    Required.  Bearer token that Copilot sends with every request.
    Requests without a valid token get a 401 response.
    If the variable is unset the endpoint is effectively open (the
    server logs a warning on startup).
``TFACTORY_WORKSPACE_ROOT``
    Optional override for the workspace root (default ``~/.tfactory``).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status as http_status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Copilot"])

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_BEARER_RE = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)
_SPEC_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _check_auth(request: Request) -> None:
    """Validate the Bearer token.  Raises 401 if invalid or missing."""
    expected = os.environ.get("COPILOT_MCP_TFACTORY_TOKEN", "").strip()
    if not expected:
        # Token not configured — warn once, allow through (dev convenience)
        logger.warning(
            "COPILOT_MCP_TFACTORY_TOKEN is not set; MCP endpoint is unauthenticated"
        )
        return

    auth_header = request.headers.get("Authorization", "")
    match = _BEARER_RE.match(auth_header)
    if not match or match.group(1) != expected:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
        )


# ---------------------------------------------------------------------------
# Workspace helpers  (mirrors tfactory_tasks.py pattern)
# ---------------------------------------------------------------------------


def _workspace_root() -> Path:
    env = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(env).expanduser() if env else Path.home() / ".tfactory"


def _find_spec_dir(task_id: str) -> Path | None:
    """Locate the spec_dir for *task_id* across all projects."""
    if not task_id or not _SPEC_ID_RE.match(task_id):
        return None
    workspaces = _workspace_root() / "workspaces"
    if not workspaces.exists():
        return None
    for project_dir in workspaces.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / "specs" / task_id
        if (candidate / "status.json").exists():
            return candidate
    return None


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_get_test_plan(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    spec_dir = _find_spec_dir(task_id)
    if spec_dir is None:
        return {"error": "task not found"}

    plan = _read_json(spec_dir / "test_plan.json")
    if plan is None:
        return {"error": "test_plan.json not yet available for this task"}

    subtasks = plan if isinstance(plan, list) else plan.get("subtasks", [])
    lanes: list[str] = sorted({s.get("lane", "") for s in subtasks if s.get("lane")})
    frameworks: dict[str, str] = {}
    for s in subtasks:
        lane = s.get("lane", "")
        fw = s.get("framework", "")
        if lane and fw and lane not in frameworks:
            frameworks[lane] = fw

    return {
        "task_id": task_id,
        "lanes": lanes,
        "frameworks": frameworks,
        "endpoints": plan.get("endpoints", {}) if isinstance(plan, dict) else {},
        "coverage_target": plan.get("coverage_target", 80) if isinstance(plan, dict) else 80,
        "mutation_scope": plan.get("mutation_scope", []) if isinstance(plan, dict) else [],
        "security_scope": plan.get("security_scope", []) if isinstance(plan, dict) else [],
        "subtask_count": len(subtasks),
    }


def _tool_get_ac_map(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    spec_dir = _find_spec_dir(task_id)
    if spec_dir is None:
        return {"error": "task not found"}

    plan = _read_json(spec_dir / "test_plan.json")
    if plan is None:
        return {"error": "test_plan.json not yet available"}

    ac_map: dict[str, list[str]] = {}
    subtasks = plan if isinstance(plan, list) else plan.get("subtasks", [])
    for subtask in subtasks:
        ac_ids = subtask.get("acceptance_criteria_ids") or subtask.get("ac_ids") or []
        files = subtask.get("source_files") or subtask.get("files") or []
        for ac_id in ac_ids:
            ac_map.setdefault(str(ac_id), []).extend(files)

    return {k: list(dict.fromkeys(v)) for k, v in ac_map.items()}


def _tool_get_coverage(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    lane = str(args.get("lane", "unit")).strip()
    spec_dir = _find_spec_dir(task_id)
    if spec_dir is None:
        return {"error": "task not found"}

    summary_path = spec_dir / "findings" / "coverage_summary.json"
    lane_summary_path = spec_dir / "findings" / f"coverage_{lane}.json"

    data: dict | None = _read_json(lane_summary_path) or _read_json(summary_path)
    if data is None:
        return {
            "lane": lane,
            "coverage_pct": None,
            "uncovered_files": [],
            "report_path": None,
            "generated_at": None,
        }

    if lane in data:
        data = data[lane]

    return {
        "lane": lane,
        "coverage_pct": data.get("coverage_pct") or data.get("line_rate"),
        "uncovered_files": data.get("uncovered_files", []),
        "report_path": data.get("report_path"),
        "generated_at": data.get("generated_at"),
    }


def _tool_get_results(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    lane = str(args.get("lane", "unit")).strip()
    spec_dir = _find_spec_dir(task_id)
    if spec_dir is None:
        return {"error": "task not found"}

    verdicts = _read_json(spec_dir / "findings" / "verdicts.json")
    if verdicts is None:
        return {"error": "no results available yet for this task"}

    lane_verdicts = [
        v for v in (verdicts if isinstance(verdicts, list) else [])
        if v.get("lane", "unit") == lane
    ]

    passed = sum(1 for v in lane_verdicts if v.get("verdict") == "accept")
    failed = sum(1 for v in lane_verdicts if v.get("verdict") == "reject")
    flagged = sum(1 for v in lane_verdicts if v.get("verdict") == "flag")

    failures = [
        {"test": v.get("test_id", ""), "error": v.get("reason", "")}
        for v in lane_verdicts if v.get("verdict") == "reject"
    ]

    return {
        "lane": lane,
        "passed": passed,
        "failed": failed,
        "flagged": flagged,
        "skipped": 0,
        "failures": failures,
        "duration_seconds": None,
        "run_at": None,
    }


def _tool_get_spec(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    spec_dir = _find_spec_dir(task_id)
    if spec_dir is None:
        return {"error": "task not found"}

    spec_paths = [
        spec_dir / "context" / "aifactory_spec.md",
        spec_dir / "context" / "spec.md",
        spec_dir / "spec.md",
    ]
    spec_md: str | None = None
    for p in spec_paths:
        if p.exists():
            try:
                spec_md = p.read_text()
            except OSError:
                pass
            break

    if spec_md is None:
        return {"error": "spec.md not found for this task"}

    ac_lines = [
        line.strip()
        for line in spec_md.splitlines()
        if re.search(r"\bAC#?\d+\b", line)
    ]

    return {
        "task_id": task_id,
        "spec_md": spec_md,
        "acceptance_criteria": ac_lines,
    }


def _tool_report_result(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    spec_dir = _find_spec_dir(task_id)
    if spec_dir is None:
        return {"error": "task not found"}

    lane = str(args.get("lane", "unit")).strip()
    passed = args.get("passed", 0)
    failed = args.get("failed", 0)
    coverage_pct = args.get("coverage_pct")
    summary = str(args.get("summary", "")).strip()

    meta_path = spec_dir / "test_task_metadata.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    results = meta.setdefault("copilot_reported_results", {})
    results[lane] = {
        "passed": passed,
        "failed": failed,
        "coverage_pct": coverage_pct,
        "summary": summary,
        "reported_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        meta_path.write_text(json.dumps(meta, indent=2))
    except OSError as exc:
        logger.warning("mcp_copilot: could not write test_task_metadata.json: %s", exc)
        return {"accepted": False, "error": str(exc)}

    logger.info(
        "mcp_copilot: tfactory_report_result task=%s lane=%s passed=%d failed=%d",
        task_id, lane, passed, failed,
    )
    return {"accepted": True}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "tfactory_get_test_plan",
        "description": "Return the test_plan.json for the given TFactory task (lanes, frameworks, coverage target, mutation scope).",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "TFactory task / spec ID"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "tfactory_get_ac_map",
        "description": "Return the acceptance-criteria → source-file mapping for a task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "tfactory_get_coverage",
        "description": "Return the latest coverage summary for a lane. Returns coverage_pct: null if no report exists yet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "lane": {"type": "string", "enum": ["unit", "api", "browser", "integration"]},
            },
            "required": ["task_id", "lane"],
        },
    },
    {
        "name": "tfactory_get_results",
        "description": "Return the most recent test-run verdicts for a lane.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "lane": {"type": "string", "enum": ["unit", "api", "browser", "integration", "security", "mutation"]},
            },
            "required": ["task_id", "lane"],
        },
    },
    {
        "name": "tfactory_get_spec",
        "description": "Return the spec.md and acceptance criteria for a task so the agent understands what to test.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "tfactory_report_result",
        "description": "Write-back: Copilot agent reports a test result after running the suite.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "lane": {"type": "string"},
                "passed": {"type": "integer"},
                "failed": {"type": "integer"},
                "coverage_pct": {"type": ["number", "null"]},
                "summary": {"type": "string"},
            },
            "required": ["task_id", "lane", "passed", "failed"],
        },
    },
]

_TOOL_DISPATCH: dict[str, Any] = {
    "tfactory_get_test_plan": _tool_get_test_plan,
    "tfactory_get_ac_map": _tool_get_ac_map,
    "tfactory_get_coverage": _tool_get_coverage,
    "tfactory_get_results": _tool_get_results,
    "tfactory_get_spec": _tool_get_spec,
    "tfactory_report_result": _tool_report_result,
}


# ---------------------------------------------------------------------------
# POST /mcp
# ---------------------------------------------------------------------------


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """Minimal MCP POST endpoint for the Copilot cloud agent.

    Handles ``initialize``, ``tools/list``, and ``tools/call`` methods.
    """
    _check_auth(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON",
        )

    method: str = body.get("method", "")
    request_id = body.get("id")

    def _ok(result: Any) -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _err(code: int, message: str) -> JSONResponse:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    if method == "initialize":
        return _ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tfactory", "version": "1.0.0"},
        })

    if method == "tools/list":
        return _ok({"tools": _TOOLS})

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}

        handler = _TOOL_DISPATCH.get(tool_name)
        if handler is None:
            return _err(-32601, f"Unknown tool: {tool_name!r}")

        try:
            result_data = handler(tool_args)
        except Exception as exc:
            logger.exception("mcp_copilot: tool %r raised unexpected error", tool_name)
            return _err(-32603, f"Internal error: {exc}")

        return _ok({
            "content": [{"type": "text", "text": json.dumps(result_data, indent=2)}],
            "isError": "error" in result_data,
        })

    return _err(-32601, f"Method not found: {method!r}")


__all__ = ["router"]
