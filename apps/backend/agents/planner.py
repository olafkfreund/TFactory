"""
Planner Agent Module
====================

Handles follow-up planner sessions for adding new subtasks to completed specs.
"""

import logging
from pathlib import Path

from core.client import create_client
from phase_config import (
    get_phase_model,
    get_phase_thinking_budget,
    get_provider_extra_kwargs,
    infer_provider_from_model,
)
from phase_event import ExecutionPhase, emit_phase
from providers.factory import get_provider
from task_logger import (
    LogPhase,
    get_task_logger,
)
from ui import (
    BuildState,
    Icons,
    StatusManager,
    bold,
    box,
    highlight,
    icon,
    muted,
    print_status,
)

from .session import run_agent_session

logger = logging.getLogger(__name__)


async def run_followup_planner(
    project_dir: Path,
    spec_dir: Path,
    model: str,
    verbose: bool = False,
) -> bool:
    """
    Run the follow-up planner to add new subtasks to a completed spec.

    This is a simplified version of run_autonomous_agent that:
    1. Creates a client
    2. Loads the followup planner prompt
    3. Runs a single planning session
    4. Returns after the plan is updated (doesn't enter coding loop)

    The planner agent will:
    - Read FOLLOWUP_REQUEST.md for the new task
    - Read the existing test_plan.json
    - Add new phase(s) with pending subtasks
    - Update the plan status back to in_progress

    Args:
        project_dir: Root directory for the project
        spec_dir: Directory containing the completed spec
        model: Claude model to use
        verbose: Whether to show detailed output

    Returns:
        bool: True if planning completed successfully
    """
    from test_plan import ImplementationPlan
    from prompts import get_followup_planner_prompt

    # Initialize status manager for ccstatusline
    status_manager = StatusManager(project_dir)
    status_manager.set_active(spec_dir.name, BuildState.PLANNING)
    emit_phase(ExecutionPhase.PLANNING, "Follow-up planning")

    # Initialize task logger for persistent logging
    task_logger = get_task_logger(spec_dir)

    # Show header
    content = [
        bold(f"{icon(Icons.GEAR)} FOLLOW-UP PLANNER SESSION"),
        "",
        f"Spec: {highlight(spec_dir.name)}",
        muted("Adding follow-up work to completed spec."),
        "",
        muted("The agent will read your FOLLOWUP_REQUEST.md and add new subtasks."),
    ]
    print()
    print(box(content, width=70, style="heavy"))
    print()

    # Start planning phase in task logger
    if task_logger:
        task_logger.start_phase(LogPhase.PLANNING, "Starting follow-up planning...")
        task_logger.set_session(1)

    # Create client with phase-specific model and thinking budget
    # Respects task_metadata.json configuration when no CLI override
    planning_model = get_phase_model(spec_dir, "planning", model)
    planning_thinking_budget = get_phase_thinking_budget(spec_dir, "planning")
    provider_name = infer_provider_from_model(planning_model)
    if provider_name == "claude":
        client = create_client(
            project_dir,
            spec_dir,
            planning_model,
            max_thinking_tokens=planning_thinking_budget,
        )
    else:
        provider_kwargs = {
            "model": planning_model,
            "working_dir": project_dir,
            **get_provider_extra_kwargs(provider_name, planning_model),
        }
        client = get_provider(
            provider_name,
            phase="planning",
            **provider_kwargs,
        )

    # Generate follow-up planner prompt
    prompt = get_followup_planner_prompt(spec_dir)

    print_status("Running follow-up planner...", "progress")
    print()

    try:
        # Run single planning session
        async with client:
            status, response, _error_info = await run_agent_session(
                client, prompt, spec_dir, verbose, phase=LogPhase.PLANNING
            )

        # End planning phase in task logger
        if task_logger:
            task_logger.end_phase(
                LogPhase.PLANNING,
                success=(status != "error"),
                message="Follow-up planning session completed",
            )

        if status == "error":
            print()
            print_status("Follow-up planning failed", "error")
            status_manager.update(state=BuildState.ERROR)
            return False

        # Verify the plan was updated (should have pending subtasks now)
        plan_file = spec_dir / "test_plan.json"
        if plan_file.exists():
            plan = ImplementationPlan.load(plan_file)

            # Check if there are any pending subtasks
            all_subtasks = [c for p in plan.phases for c in p.subtasks]
            pending_subtasks = [c for c in all_subtasks if c.status.value == "pending"]

            if pending_subtasks:
                # Reset the plan status to in_progress (in case planner didn't)
                plan.reset_for_followup()
                plan.save(plan_file)

                print()
                content = [
                    bold(f"{icon(Icons.SUCCESS)} FOLLOW-UP PLANNING COMPLETE"),
                    "",
                    f"New pending subtasks: {highlight(str(len(pending_subtasks)))}",
                    f"Total subtasks: {len(all_subtasks)}",
                    "",
                    muted("Next steps:"),
                    f"  Run: {highlight(f'python tfactory/run.py --spec {spec_dir.name}')}",
                ]
                print(box(content, width=70, style="heavy"))
                print()
                status_manager.update(state=BuildState.PAUSED)
                return True
            else:
                print()
                print_status(
                    "Warning: No pending subtasks found after planning", "warning"
                )
                print(muted("The planner may not have added new subtasks."))
                print(muted("Check test_plan.json manually."))
                status_manager.update(state=BuildState.PAUSED)
                return False
        else:
            print()
            print_status(
                "Error: test_plan.json not found after planning", "error"
            )
            status_manager.update(state=BuildState.ERROR)
            return False

    except Exception as e:
        print()
        print_status(f"Follow-up planning error: {e}", "error")
        if task_logger:
            task_logger.log_error(f"Follow-up planning error: {e}", LogPhase.PLANNING)
        status_manager.update(state=BuildState.ERROR)
        return False


# ---------------------------------------------------------------------------
# TFactory Planner (Task 5, #6) — STUB at commit 2 of 6.
#
# Real Claude-Agent-SDK wiring lands in commit 4. This stub just demonstrates
# the auto-fire scheduling end-to-end:
#   - status.json: pending → planning → planned
#   - test_plan.json: minimal valid empty plan written
#
# Imports are deliberately scoped to stdlib + local modules so the stub runs
# without claude-agent-sdk available — keeps the auto-fire path testable in
# the minimal venv setup we used for commit 1's verification pass.
# ---------------------------------------------------------------------------

import asyncio
import json
import logging as _logging
import os
import traceback
from datetime import datetime, timezone
from typing import Literal

_planner_log = _logging.getLogger(__name__ + ".tfactory")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_status(spec_dir: Path) -> dict:
    """Read status.json or return an empty dict if missing/corrupt."""
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    """Merge ``fields`` into status.json (atomic-ish single-file write)."""
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))


async def run_planner(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "replan"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Planner agent — STUB at commit 2/6.

    Args:
        spec_dir: TFactory workspace spec dir (``~/.tfactory/workspaces/.../specs/<id>/``).
        project_dir: AIFactory project root_path (unused in stub; real
            planner uses for Glob/Grep tools in commit 4).
        mode: 'initial' or 'replan'. Stub ignores; real impl branches.
        verbose: forwarded to ``run_agent_session`` once the real impl lands.

    Returns:
        True on success; False if the workspace is malformed.

    Stub behavior:
        - status.json: status=planning
        - test_plan.json: minimal valid empty plan
        - status.json: status=planned (or planner_failed on exception)
    """
    if not spec_dir.is_dir():
        _planner_log.error("planner: spec_dir %s does not exist", spec_dir)
        return False

    try:
        _write_status_patch(
            spec_dir, status="planning", phase=f"planner_{mode}_started"
        )

        # Yield to the event loop so callers can observe the planning state
        # before we transition to planned. Real impl spends seconds-to-minutes
        # here; the stub just touches the loop.
        await asyncio.sleep(0)

        # Emit a minimal valid ImplementationPlan. Use the test_plan module
        # so the JSON shape stays in lock-step with the model the rest of
        # the pipeline will load via ImplementationPlan.load().
        from test_plan import ImplementationPlan, WorkflowType  # local import: avoid SDK load cost

        plan = ImplementationPlan(
            feature=f"<stub planner — {spec_dir.name}>",
            workflow_type=WorkflowType.FEATURE,
            services_involved=[],
            phases=[],
            final_acceptance=[],
            created_at=_now_iso(),
            updated_at=_now_iso(),
            status="in_progress",
            planStatus="pending",
        )
        plan.save(spec_dir / "test_plan.json")

        _write_status_patch(
            spec_dir,
            status="planned_empty",
            phase=f"planner_{mode}_stub_complete",
            planner_warnings=[
                "stub planner (commit 2/6) — empty plan emitted; real agent lands in commit 4"
            ],
        )
        return True

    except Exception as exc:
        _planner_log.error("planner stub failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="planner_failed",
            phase=f"planner_{mode}_error",
            planner_error=str(exc)[:500],
        )
        return False


# Module-level set so asyncio.create_task'd planner runs aren't GC'd while
# the scheduling caller returns. Each completed task is removed via the
# `done_callback`. Auto-fire path in task_control.py uses this directly.
_BG_PLANNER_TASKS: set[asyncio.Task] = set()


def schedule_planner(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "replan"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget the planner, gated by TFACTORY_AUTO_PLAN env var.

    Returns the asyncio.Task that was scheduled, or None if auto-plan is
    disabled. Caller doesn't need to await; the task is GC-anchored in
    ``_BG_PLANNER_TASKS`` until it completes.
    """
    if os.environ.get("TFACTORY_AUTO_PLAN", "1") == "0":
        return None
    task = asyncio.create_task(run_planner(spec_dir, project_dir, mode=mode))
    _BG_PLANNER_TASKS.add(task)
    task.add_done_callback(_BG_PLANNER_TASKS.discard)
    return task
