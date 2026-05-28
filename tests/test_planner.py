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


@pytest.fixture(autouse=True)
def _disable_auto_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin TFACTORY_AUTO_GENERATE=0 for the whole module.

    Without this, the planner's success path schedules Gen-Functional
    (Task 6, #7); the stub would fire concurrently and mutate
    workspace state under the planner tests' assertions. Tests that
    actually exercise the planner→gen_functional chain set
    TFACTORY_AUTO_GENERATE=1 explicitly.
    """
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")


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


# ── Replan mode (commit 5) ──────────────────────────────────────────────


def _write_replan_request(spec_dir: Path, subtask_id: str,
                           reason: str = "hallucinated import",
                           failed_target: str = "foo.py::nope") -> None:
    """Drop a context/replan_request.json — what Gen-Functional writes."""
    (spec_dir / "context" / "replan_request.json").write_text(json.dumps({
        "subtask_id": subtask_id,
        "reason": reason,
        "failed_target": failed_target,
    }))


def _make_plan_with_replan_phase(
    original_subtask_id: str = "s0",
    replan_id: str = "s0-r1",
    replan_phase_name: str = "replan-1",
    original_replan_count_after: int = 0,
) -> str:
    """Build a plan that mimics the agent's post-replan output:
    original plan + appended replan-N phase. The agent does NOT
    bump replan_count itself — our post-session helper does that.
    """
    return json.dumps({
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1, "name": "AC#1: works", "type": "implementation",
                "subtasks": [{
                    "id": original_subtask_id, "description": "orig",
                    "status": "pending", "lane": "functional",
                    "target": "foo.py::bar", "rationale": "AC#1",
                    "files_to_create": ["tests/test_orig.py"],
                    "verification": {
                        "type": "command",
                        "command": "pytest tests/test_orig.py",
                        "expected": "exit 0",
                    },
                    # Pre-existing replan_count from earlier rounds.
                    "replan_count": original_replan_count_after,
                }],
                "parallel_safe": False,
            },
            {
                "phase": 2, "name": replan_phase_name, "type": "implementation",
                "subtasks": [{
                    "id": replan_id, "description": "corrected",
                    "status": "pending", "lane": "functional",
                    "target": "foo.py::real_func", "rationale":
                        f"Replan of '{original_subtask_id}': original failed",
                    "files_to_create": ["tests/test_corrected.py"],
                    "verification": {
                        "type": "command",
                        "command": "pytest tests/test_corrected.py",
                        "expected": "exit 0",
                    },
                }],
                "parallel_safe": False,
            },
        ],
        "final_acceptance": [],
        "status": "in_progress", "planStatus": "pending",
    })


@pytest.mark.asyncio
async def test_replan_happy_path_appends_phase_and_bumps_count(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """First replan: phase appended, count goes 0 → 1, not stuck yet."""
    # Pre-seed the spec dir with an existing plan + replan request.
    (spec_dir / "test_plan.json").write_text(_make_valid_plan_json(1))
    _write_replan_request(spec_dir, "s0")

    # Mock the agent emitting the same plan + a new replan-1 phase.
    mock_sdk(plans=[_make_plan_with_replan_phase("s0")])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"
    assert "replan_complete" in status["phase"]
    assert status["last_replan_for"] == "s0"
    assert status["last_replan_count"] == 1
    assert status["last_replan_stuck"] is False

    # Plan persisted with bumped count on the original subtask.
    final_plan = json.loads((spec_dir / "test_plan.json").read_text())
    s0 = final_plan["phases"][0]["subtasks"][0]
    assert s0["id"] == "s0"
    assert s0["replan_count"] == 1


@pytest.mark.asyncio
async def test_replan_second_round_hits_stuck(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """Second replan on the same subtask flips status to stuck."""
    # Pre-seed: original subtask already has replan_count=1 from a prior round.
    pre_seed = json.loads(_make_valid_plan_json(1))
    pre_seed["phases"][0]["subtasks"][0]["replan_count"] = 1
    (spec_dir / "test_plan.json").write_text(json.dumps(pre_seed))
    _write_replan_request(spec_dir, "s0")

    # Mock the agent emitting the plan with another replan phase appended.
    # Pre-existing replan_count is 1; the bumper takes it to 2.
    plan_with_replan = json.loads(_make_plan_with_replan_phase("s0"))
    plan_with_replan["phases"][0]["subtasks"][0]["replan_count"] = 1
    mock_sdk(plans=[json.dumps(plan_with_replan)])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["last_replan_count"] == 2
    assert status["last_replan_stuck"] is True
    assert any("stuck" in w for w in status.get("planner_warnings", []))


@pytest.mark.asyncio
async def test_replan_missing_replan_request_fails(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """No context/replan_request.json → planner_failed before SDK invoke."""
    (spec_dir / "test_plan.json").write_text(_make_valid_plan_json(1))
    calls = mock_sdk(plans=[_make_plan_with_replan_phase("s0")])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is False
    assert len(calls) == 0  # SDK never invoked
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert "missing_request" in status["phase"]


@pytest.mark.asyncio
async def test_replan_missing_existing_plan_fails(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """No existing test_plan.json → planner_failed before SDK invoke."""
    _write_replan_request(spec_dir, "s0")
    calls = mock_sdk(plans=[_make_plan_with_replan_phase("s0")])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is False
    assert len(calls) == 0
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert "no_existing_plan" in status["phase"]


@pytest.mark.asyncio
async def test_replan_warns_when_subtask_id_unknown(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """If replan_request.subtask_id doesn't match any subtask, succeed
    but emit a warning (the agent did its job; our bookkeeping just
    can't find the target)."""
    (spec_dir / "test_plan.json").write_text(_make_valid_plan_json(1))
    _write_replan_request(spec_dir, "ghost-subtask")
    mock_sdk(plans=[_make_plan_with_replan_phase("s0")])  # plan still has s0

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert any("not found" in w for w in status.get("planner_warnings", []))


@pytest.mark.asyncio
async def test_replan_rejects_when_existing_phases_dropped(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    """If the agent emits a plan that drops earlier phases, fail clearly."""
    # Pre-seed a plan with TWO phases.
    pre = json.loads(_make_valid_plan_json(2))
    pre["phases"].append({
        "phase": 2, "name": "AC#2", "type": "implementation",
        "subtasks": [], "parallel_safe": False,
    })
    (spec_dir / "test_plan.json").write_text(json.dumps(pre))
    _write_replan_request(spec_dir, "s0")

    # Mock the agent emitting a plan that LOST phase 2 (only kept phase 1 + new replan-3)
    # — a regression we explicitly defend against.
    bad = json.loads(_make_valid_plan_json(1))
    bad["phases"].append({
        "phase": 3, "name": "replan-1", "type": "implementation",
        "subtasks": [{
            "id": "s0-r1", "description": "x", "status": "pending",
            "lane": "functional", "target": "f.py::g", "rationale": "r",
            "files_to_create": ["tests/x.py"],
            "verification": {"type": "command", "command": "pytest",
                             "expected": "exit 0"},
        }],
        "parallel_safe": False,
    })
    mock_sdk(plans=[json.dumps(bad)])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert "phases_lost" in status["phase"]
    assert "dropped existing phases" in status.get("planner_error", "")


@pytest.mark.asyncio
async def test_replan_session_error_no_retry(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    (spec_dir / "test_plan.json").write_text(_make_valid_plan_json(1))
    _write_replan_request(spec_dir, "s0")
    calls = mock_sdk(plans=[None], statuses=["error"])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is False
    assert len(calls) == 1
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planner_failed"
    assert status["phase"] == "planner_replan_session_error"


@pytest.mark.asyncio
async def test_replan_retry_succeeds_after_invalid_json(
    spec_dir: Path, project_dir: Path, mock_sdk
) -> None:
    (spec_dir / "test_plan.json").write_text(_make_valid_plan_json(1))
    _write_replan_request(spec_dir, "s0")
    mock_sdk(plans=["bad json {{{", _make_plan_with_replan_phase("s0")])

    ok = await run_planner(spec_dir, project_dir, mode="replan")
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["last_replan_count"] == 1


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
