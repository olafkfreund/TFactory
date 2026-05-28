"""Tests for the Planner stub + auto-fire scheduler — Task 5 (#6) commit 2.

The stub (commit 2) exercises the auto-fire wiring end-to-end without
the real Claude Agent SDK session. Real planner behavior lands in commit
4 and gets its own test file (`test_planner.py`).

Covered:
  - Direct stub `run_planner` happy path (no scheduling)
  - status.json transitions (pending → planning → planned_empty)
  - test_plan.json emitted as a valid ImplementationPlan
  - schedule_planner respects TFACTORY_AUTO_PLAN env (1 → task, 0 → None)
  - schedule_planner anchors the task in _BG_PLANNER_TASKS until done
  - Failure path: spec_dir doesn't exist → returns False, no crash
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Pre-mock the inherited backend surface so importing agents.planner
# doesn't drag in claude_agent_sdk / providers / phase_config.
_PREMOCK = [
    "claude_agent_sdk", "claude_agent_sdk.types",
    "core.client", "core.workspace",
    "phase_config", "phase_event", "providers.factory", "task_logger",
    "agents.memory_manager", "agents.session", "recovery",
    "workspace", "worktree",
]
for _m in _PREMOCK:
    sys.modules.setdefault(_m, MagicMock())

from agents.planner import (  # noqa: E402
    _BG_PLANNER_TASKS,
    run_planner,
    schedule_planner,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    """A workspace spec dir as task_create_and_run would have created."""
    d = tmp_path / "workspaces" / "demo" / "specs" / "001"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(json.dumps({
        "task_id": "001",
        "project_id": "demo",
        "spec_id": "001",
        "status": "pending",
        "phase": "created",
    }))
    return d


# ── Direct stub run ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_returns_true_for_valid_spec_dir(spec_dir: Path) -> None:
    ok = await run_planner(spec_dir, project_dir=Path("/tmp"))
    assert ok is True


@pytest.mark.asyncio
async def test_stub_transitions_status_to_planned_empty(spec_dir: Path) -> None:
    await run_planner(spec_dir, project_dir=Path("/tmp"))
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned_empty"
    assert "stub" in (status.get("planner_warnings") or [""])[0]


@pytest.mark.asyncio
async def test_stub_emits_valid_empty_plan(spec_dir: Path) -> None:
    await run_planner(spec_dir, project_dir=Path("/tmp"))
    plan_file = spec_dir / "test_plan.json"
    assert plan_file.exists()
    plan = json.loads(plan_file.read_text())
    assert "feature" in plan
    assert plan.get("phases") == []
    # workflow_type defaulted by the model
    assert "workflow_type" in plan


@pytest.mark.asyncio
async def test_stub_records_updated_at(spec_dir: Path) -> None:
    await run_planner(spec_dir, project_dir=Path("/tmp"))
    status = json.loads((spec_dir / "status.json").read_text())
    assert "updated_at" in status


# ── Failure path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_returns_false_for_missing_spec_dir(tmp_path: Path) -> None:
    ghost = tmp_path / "does" / "not" / "exist"
    ok = await run_planner(ghost, project_dir=Path("/tmp"))
    assert ok is False


# ── schedule_planner env gating ──────────────────────────────────────────


def test_schedule_planner_disabled_returns_none(
    spec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    # Need a running loop for asyncio.create_task; use asyncio.run wrapper
    async def _run():
        return schedule_planner(spec_dir, Path("/tmp"))
    result = asyncio.run(_run())
    assert result is None


@pytest.mark.asyncio
async def test_schedule_planner_enabled_returns_task(
    spec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "1")
    task = schedule_planner(spec_dir, Path("/tmp"))
    assert task is not None
    assert isinstance(task, asyncio.Task)
    await task
    # Status should have advanced
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned_empty"


@pytest.mark.asyncio
async def test_scheduled_task_is_gc_anchored(
    spec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module-level set keeps tasks alive until done; cleared after."""
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "1")
    before = len(_BG_PLANNER_TASKS)
    task = schedule_planner(spec_dir, Path("/tmp"))
    assert task is not None
    # Task is anchored while running
    assert len(_BG_PLANNER_TASKS) == before + 1
    await task
    # done_callback discards it
    assert len(_BG_PLANNER_TASKS) == before
