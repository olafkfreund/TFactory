"""Inbound AIFactory completion webhook — closes the automatic
fail→handback→fix→re-test loop (epic #182).

AIFactory POSTs here when its QA Fixer finishes correcting a feature TFactory
handed back. We correlate the call to the TFactory workspace, apply the bounded
loop guard (``agents.handback.loop``), and either re-fire the pipeline
(``agents.handback.rerun.rerun_pipeline``) or mark the task ``stuck``.

The whole endpoint is gated by ``APP_INBOUND_HANDBACK_ENABLED`` and a shared
secret in the ``X-TFactory-Handback-Token`` header — the auth middleware exempts
``/api/handback/`` (the caller is AIFactory's server, not a portal user), so this
handler authenticates itself.

Handler is ``async def`` so the in-process ``schedule_planner`` (asyncio task)
fires on the running uvicorn event loop.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel

from ..config import get_settings

# Make apps/backend importable for the loop guard + rerun core.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

router = APIRouter()

_SPEC_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Statuses that mean a run is already in flight — don't double-fire the pipeline.
_ACTIVE_STATUSES = frozenset(
    {
        "pending",
        "planning",
        "generating",
        "executing",
        "evaluating",
        "triaging",
        "running",
        "in_progress",
    }
)


class AIFactoryCompletePayload(BaseModel):
    """Body AIFactory POSTs when its QA Fixer finishes a correction."""

    tfactory_task_id: str | None = None  # "project_id:spec_id" (echoed back)
    project_id: str | None = None  # alternative to tfactory_task_id
    spec_id: str | None = None
    status: str = "complete"  # "complete" | "failed"
    lane: str = "unit"


def _resolve_workspace_root() -> Path:
    env_val = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(env_val).expanduser() if env_val else Path.home() / ".tfactory"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _resolve_ids(payload: AIFactoryCompletePayload) -> tuple[str, str]:
    """Derive (project_id, spec_id) from the payload, with traversal guards."""
    if payload.tfactory_task_id:
        if ":" not in payload.tfactory_task_id:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="tfactory_task_id must be 'project_id:spec_id'",
            )
        project_id, spec_id = payload.tfactory_task_id.split(":", 1)
    elif payload.project_id and payload.spec_id:
        project_id, spec_id = payload.project_id, payload.spec_id
    else:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="provide tfactory_task_id or project_id + spec_id",
        )
    for part in (project_id, spec_id):
        if not part or not _SPEC_ID_RE.match(part):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"invalid id component: {part!r}",
            )
    return project_id, spec_id


def _mark_stuck(spec_dir: Path, reason: str) -> None:
    """Flag the task stuck for human review (cap hit / no progress)."""
    status_file = spec_dir / "status.json"
    status = _read_json(status_file) or {}
    status["status"] = "stuck"
    status["stuck_reason"] = reason
    status_file.write_text(json.dumps(status, indent=2))


@router.post("/aifactory-complete")
async def aifactory_complete(
    payload: AIFactoryCompletePayload,
    x_tfactory_handback_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive AIFactory's "fix done" signal and auto-re-test (bounded)."""
    settings = get_settings()
    if not settings.INBOUND_HANDBACK_ENABLED:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="inbound handback webhook disabled",
        )

    # Shared-secret auth (the middleware exempts /api/handback/).
    secret = settings.INBOUND_HANDBACK_SECRET
    if not secret or x_tfactory_handback_token != secret:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="invalid handback token",
        )

    project_id, spec_id = _resolve_ids(payload)
    task_id = f"{project_id}:{spec_id}"
    spec_dir = _resolve_workspace_root() / "workspaces" / project_id / "specs" / spec_id
    if not (spec_dir / "status.json").exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"unknown task {task_id}",
        )

    from agents.handback.loop import (
        decide_loop,
        failure_signature,
        read_loop_state,
        record_cycle,
    )
    from agents.handback.rerun import rerun_pipeline

    # Failing set from the run that triggered the handback vs. the failures
    # recorded at the previous correction (loop progress fingerprint).
    verdicts = _read_json(spec_dir / "findings" / "verdicts.json") or {}
    current = failure_signature(verdicts)
    cycle, previous = read_loop_state(spec_dir)
    decision = decide_loop(
        cycle=cycle, current_failures=current, previous_failures=previous
    )

    if decision.action == "passed":
        return {"action": "passed", "reason": decision.reason, "task_id": task_id}

    if decision.action == "stuck":
        _mark_stuck(spec_dir, decision.reason)
        return {"action": "stuck", "reason": decision.reason, "task_id": task_id}

    # retest — guard against double-firing if a run is already in flight.
    status = _read_json(spec_dir / "status.json") or {}
    if status.get("status") in _ACTIVE_STATUSES:
        return {
            "action": "already_running",
            "status": status.get("status"),
            "task_id": task_id,
        }

    record_cycle(spec_dir, cycle=cycle + 1, failure_signature=current)
    result = rerun_pipeline(
        project_id, spec_id, lane=payload.lane, root=_resolve_workspace_root()
    )
    return {
        "action": "retest",
        "reason": decision.reason,
        "cycle": cycle + 1,
        "planner_scheduled": result["planner_scheduled"],
        "task_id": task_id,
    }
