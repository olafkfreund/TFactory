"""Tests for the real TFactory Planner agent — Task 5 (#6) commit 4.

The real `run_planner` invokes the Claude Agent SDK; these tests mock
the two SDK seams (`_resolve_planner_client` + `_invoke_session`) so we
exercise the orchestration without burning tokens or needing an API key.

Covered:
  - Happy path: valid plan emitted in one session → status=planned
  - Empty plan: 0 subtasks → status=planned_empty (warning, not failure)
  - Over-budget: 35 subtasks → truncated to 30 + warning
  - Soft warning above 15 subtasks
  - Missing file → retry → valid plan → status=planned
  - Missing file → retry → still missing → status=planner_failed
  - Invalid JSON → retry → valid → status=planned
  - Invalid JSON → retry → still invalid → status=planner_failed
  - Session error → status=planner_failed (no retry)
  - Replan mode: deferred to commit 5 → returns False with clear status
  - Missing spec_dir → returns False, no crash

Plus schedule_planner + _BG_PLANNER_TASKS lifecycle (unchanged since
commit 2; verified here against the real planner).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agents.planner import (
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
    # Minimum context the prompt helper references
    (d / "context" / "aifactory_spec.md").write_text("# spec\n\n## ACs\n- AC#1: works\n")
    (d / "context" / "source.json").write_text("{}")
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


def _make_valid_plan_json(subtask_count: int = 1) -> str:
    """Build a valid ImplementationPlan JSON string with N functional subtasks."""
    return json.dumps({
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "AC#1: works",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": f"s{i}",
                        "description": f"test {i}",
                        "status": "pending",
                        "lane": "functional",
                        "target": f"foo.py::bar{i}",
                        "rationale": "AC#1",
                        "files_to_create": [f"tests/test_{i}.py"],
                        "verification": {
                            "type": "command",
                            "command": f"pytest tests/test_{i}.py",
                            "expected": "exit 0",
                        },
                    }
                    for i in range(subtask_count)
                ],
                "parallel_safe": False,
            },
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    })


def _make_over_budget_plan_json(subtask_count: int = 35) -> str:
    """Plan with too many subtasks — should be truncated."""
    per_phase = (subtask_count + 2) // 3
    phases = []
    sid = 0
    remaining = subtask_count
    for ph in range(3):
        n = min(per_phase, remaining)
        phases.append({
            "phase": ph + 1,
            "name": f"AC#{ph + 1}",
            "type": "implementation",
            "subtasks": [
                {
                    "id": f"s{sid + i}",
                    "description": f"test {sid + i}",
                    "status": "pending",
                    "lane": "functional",
                    "target": f"foo.py::bar{sid + i}",
                    "rationale": f"AC#{ph + 1}",
                    "files_to_create": [f"tests/test_{sid + i}.py"],
                    "verification": {
                        "type": "command",
                        "command": f"pytest tests/test_{sid + i}.py",
                        "expected": "exit 0",
                    },
                }
                for i in range(n)
            ],
            "parallel_safe": False,
        })
        sid += n
        remaining -= n
    return json.dumps({
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": phases,
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    })


@pytest.fixture
def mock_sdk(monkeypatch: pytest.MonkeyPatch):
    """Patch the two SDK seams: client resolution + session invocation.

    Returns a setup function. Call it with the canned plan contents per
    session call (None = no Write) and per-call session statuses.
    """
    call_log: list[dict] = []

    class _FakeAsyncCM:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None

    def _setup(*, plans=None, statuses=None):
        plans = list(plans) if plans is not None else [None]
        statuses = list(statuses) if statuses is not None else ["complete"] * len(plans)
        plans_iter = iter(plans)
        statuses_iter = iter(statuses)

        async def _resolve(*a, **kw):
            return _FakeAsyncCM()

        async def _invoke(client, prompt, spec_dir_arg, verbose):
            call_log.append({
                "prompt": prompt,
                "spec_dir": str(spec_dir_arg),
                "verbose": verbose,
            })
            try:
                canned = next(plans_iter)
            except StopIteration:
                canned = None
            try:
                status = next(statuses_iter)
            except StopIteration:
                status = "complete"
            if canned is not None:
                (spec_dir_arg / "test_plan.json").write_text(canned)
            return status, "mock response", {}

        monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
        monkeypatch.setattr("agents.planner._invoke_session", _invoke)
        return call_log

    return _setup


# ── Happy path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_happy_path_emits_planned(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    mock_sdk(plans=[_make_valid_plan_json(2)])
    ok = await run_planner(spec_dir, project_dir, mode="initial")
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["subtask_count"] == 2
    assert (spec_dir / "test_plan.json").exists()


@pytest.mark.asyncio
async def test_initial_invokes_session_once_on_happy(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    calls = mock_sdk(plans=[_make_valid_plan_json(1)])
    await run_planner(spec_dir, project_dir)
    assert len(calls) == 1  # no retry needed


# ── Empty plan ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_empty_plan_is_warning_not_failure(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    mock_sdk(plans=[_make_valid_plan_json(0)])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is True  # warning, not failure
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned_empty"
    assert any("0 subtasks" in w for w in status.get("planner_warnings", []))


# ── Over-budget truncation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_truncates_over_budget(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    mock_sdk(plans=[_make_over_budget_plan_json(35)])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["subtask_count"] == 30
    assert any("truncated to 30" in w for w in status.get("planner_warnings", []))


@pytest.mark.asyncio
async def test_initial_soft_warning_above_15(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    mock_sdk(plans=[_make_over_budget_plan_json(20)])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["subtask_count"] == 20
    assert any("soft warning above 15" in w for w in status.get("planner_warnings", []))


# ── Missing file → retry ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_retry_succeeds_after_missing_file(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    calls = mock_sdk(plans=[None, _make_valid_plan_json(1)])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is True
    assert len(calls) == 2
    # Retry prompt should mention the Write tool / test_plan.json
    assert "Write" in calls[1]["prompt"] or "test_plan.json" in calls[1]["prompt"]
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"


@pytest.mark.asyncio
async def test_initial_fails_when_retry_also_misses(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    calls = mock_sdk(plans=[None, None])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is False
    assert len(calls) == 2
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert "missing" in status["phase"]


# ── Invalid JSON → retry ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_retry_succeeds_after_invalid_json(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    mock_sdk(plans=["not valid json {{{", _make_valid_plan_json(1)])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"


@pytest.mark.asyncio
async def test_initial_fails_when_retry_also_invalid_json(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    mock_sdk(plans=["bad {{{", "still bad ["])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert "json" in status["phase"]


# ── Session-error path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_session_error_no_retry(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    calls = mock_sdk(plans=[None], statuses=["error"])
    ok = await run_planner(spec_dir, project_dir)
    assert ok is False
    assert len(calls) == 1  # no retry on session-level error
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert status["phase"] == "planner_session_error"


# ── Replan mode (deferred to commit 5) ──────────────────────────────────


@pytest.mark.asyncio
async def test_replan_mode_deferred_in_commit_4(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """Replan mode returns False with a clear deferred status until commit 5."""
    mock_sdk(plans=[_make_valid_plan_json(1)])
    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert "replan_not_implemented" in status["phase"]


# ── Failure paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_false_for_missing_spec_dir(
    tmp_path: Path, project_dir: Path
) -> None:
    ghost = tmp_path / "does" / "not" / "exist"
    ok = await run_planner(ghost, project_dir)
    assert ok is False


# ── schedule_planner — unchanged surface from commit 2 ──────────────────


def test_schedule_planner_disabled_returns_none(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    async def _run():
        return schedule_planner(spec_dir, project_dir)
    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_planner_enabled_returns_task(
    spec_dir: Path, project_dir: Path, mock_sdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "1")
    mock_sdk(plans=[_make_valid_plan_json(1)])
    task = schedule_planner(spec_dir, project_dir)
    assert task is not None
    assert isinstance(task, asyncio.Task)
    await task
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"


@pytest.mark.asyncio
async def test_scheduled_task_is_gc_anchored_then_cleared(
    spec_dir: Path, project_dir: Path, mock_sdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "1")
    mock_sdk(plans=[_make_valid_plan_json(1)])
    before = len(_BG_PLANNER_TASKS)
    task = schedule_planner(spec_dir, project_dir)
    assert task is not None
    assert len(_BG_PLANNER_TASKS) == before + 1
    await task
    assert len(_BG_PLANNER_TASKS) == before
