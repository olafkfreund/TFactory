"""Tests for the Gen-Functional stub + auto-advance — Task 6 (#7) commit 1.

The stub (commit 1) exercises the planner→gen_functional auto-advance
wiring end-to-end without the real SDK. Real generation behaviour
lands in commits 2-5 of Task 6.

Covered:
  - Direct stub `run_gen_functional` — status transitions to generated_empty
  - Returns False on missing spec_dir
  - schedule_gen_functional honours TFACTORY_AUTO_GENERATE env (1 → task, 0 → None)
  - _BG_GEN_FUNCTIONAL_TASKS anchors the task until done
  - Planner success path auto-schedules Gen-Functional (the chain)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agents.gen_functional import (
    _BG_GEN_FUNCTIONAL_TASKS,
    run_gen_functional,
    schedule_gen_functional,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    """Workspace as a successful planner run would have left it."""
    d = tmp_path / "workspaces" / "demo" / "specs" / "001"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(json.dumps({
        "task_id": "001",
        "project_id": "demo",
        "status": "planned",
        "phase": "planner_initial_complete",
        "subtask_count": 3,
    }))
    (d / "test_plan.json").write_text(json.dumps({
        "feature": "test", "workflow_type": "feature",
        "services_involved": [],
        "phases": [{
            "phase": 1, "name": "AC#1", "type": "implementation",
            "subtasks": [], "parallel_safe": False,
        }],
        "final_acceptance": [], "status": "in_progress", "planStatus": "pending",
    }))
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


# ── Direct stub ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_returns_true_for_valid_spec_dir(
    spec_dir: Path, project_dir: Path,
) -> None:
    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True


@pytest.mark.asyncio
async def test_stub_transitions_to_generated_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    await run_gen_functional(spec_dir, project_dir)
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated_empty"
    assert status["tests_generated"] == 0
    assert any("stub" in w for w in status.get("gen_functional_warnings", []))


@pytest.mark.asyncio
async def test_stub_warns_when_plan_missing(
    spec_dir: Path, project_dir: Path,
) -> None:
    (spec_dir / "test_plan.json").unlink()
    await run_gen_functional(spec_dir, project_dir)
    status = json.loads((spec_dir / "status.json").read_text())
    assert any(
        "test_plan.json missing" in w
        for w in status.get("gen_functional_warnings", [])
    )


@pytest.mark.asyncio
async def test_returns_false_for_missing_spec_dir(
    tmp_path: Path, project_dir: Path,
) -> None:
    ghost = tmp_path / "does" / "not" / "exist"
    ok = await run_gen_functional(ghost, project_dir)
    assert ok is False


# ── schedule_gen_functional env gating ──────────────────────────────────


def test_schedule_disabled_returns_none(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")
    async def _run():
        return schedule_gen_functional(spec_dir, project_dir)
    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "1")
    task = schedule_gen_functional(spec_dir, project_dir)
    assert task is not None
    assert isinstance(task, asyncio.Task)
    await task
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated_empty"


@pytest.mark.asyncio
async def test_scheduled_task_is_gc_anchored(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "1")
    before = len(_BG_GEN_FUNCTIONAL_TASKS)
    task = schedule_gen_functional(spec_dir, project_dir)
    assert task is not None
    assert len(_BG_GEN_FUNCTIONAL_TASKS) == before + 1
    await task
    assert len(_BG_GEN_FUNCTIONAL_TASKS) == before


# ── Chain test: planner success → gen_functional schedule ───────────────


@pytest.mark.asyncio
async def test_planner_success_schedules_gen_functional(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a successful planner run auto-fires the gen_functional stub."""
    # Allow both auto-fire env vars.
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "1")

    # Mock the planner's SDK seams so we don't burn tokens.
    class _FakeAsyncCM:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    async def _resolve(*a, **kw): return _FakeAsyncCM()
    async def _invoke(client, prompt, spec_dir_arg, verbose):
        (spec_dir_arg / "test_plan.json").write_text(json.dumps({
            "feature": "x", "workflow_type": "feature",
            "services_involved": [],
            "phases": [{
                "phase": 1, "name": "AC#1", "type": "implementation",
                "subtasks": [{
                    "id": "s0", "description": "x", "status": "pending",
                    "lane": "functional", "target": "f.py::g",
                    "rationale": "AC#1",
                    "files_to_create": ["tests/test_x.py"],
                    "verification": {
                        "type": "command", "command": "pytest tests/test_x.py",
                        "expected": "exit 0",
                    },
                }],
                "parallel_safe": False,
            }],
            "final_acceptance": [], "status": "in_progress", "planStatus": "pending",
        }))
        return "complete", "mock", {}
    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)

    from agents.planner import run_planner
    ok = await run_planner(spec_dir, project_dir, mode="initial")
    assert ok is True

    # Drain the scheduled gen_functional task so the chain completes.
    if _BG_GEN_FUNCTIONAL_TASKS:
        await asyncio.gather(*list(_BG_GEN_FUNCTIONAL_TASKS), return_exceptions=True)

    # After the chain: planner left status=planned briefly; gen_functional
    # advanced it to generated_empty.
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated_empty"
    # Planner's metadata still present (gen_functional layered its own warnings).
    assert status["subtask_count"] == 1


@pytest.mark.asyncio
async def test_planner_success_does_not_schedule_when_disabled(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When AUTO_GENERATE=0, planner leaves status=planned without advancing."""
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")

    class _FakeAsyncCM:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    async def _resolve(*a, **kw): return _FakeAsyncCM()
    async def _invoke(client, prompt, spec_dir_arg, verbose):
        (spec_dir_arg / "test_plan.json").write_text(json.dumps({
            "feature": "x", "workflow_type": "feature",
            "services_involved": [],
            "phases": [{
                "phase": 1, "name": "AC#1", "type": "implementation",
                "subtasks": [{
                    "id": "s0", "description": "x", "status": "pending",
                    "lane": "functional", "target": "f.py::g",
                    "rationale": "AC#1", "files_to_create": ["tests/x.py"],
                    "verification": {"type": "command",
                                     "command": "pytest tests/x.py",
                                     "expected": "exit 0"},
                }],
                "parallel_safe": False,
            }],
            "final_acceptance": [], "status": "in_progress", "planStatus": "pending",
        }))
        return "complete", "mock", {}
    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)

    from agents.planner import run_planner
    ok = await run_planner(spec_dir, project_dir, mode="initial")
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    # Stays at planned (gen_functional never fired)
    assert status["status"] == "planned"
