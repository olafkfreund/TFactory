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


# Subtask cap — anything above is truncated post-emit with a warning.
_HARD_SUBTASK_CAP = 30
_SOFT_SUBTASK_WARN = 15


def _count_subtasks(plan) -> int:
    """Total subtasks across all phases."""
    return sum(len(p.subtasks) for p in plan.phases)


def _truncate_subtasks(plan, cap: int) -> int:
    """Drop subtasks past ``cap`` (keeping phase ordering).

    Returns the number of subtasks dropped.
    """
    dropped = 0
    keep = cap
    for phase in plan.phases:
        if keep <= 0:
            dropped += len(phase.subtasks)
            phase.subtasks = []
            continue
        if len(phase.subtasks) > keep:
            dropped += len(phase.subtasks) - keep
            phase.subtasks = phase.subtasks[:keep]
            keep = 0
        else:
            keep -= len(phase.subtasks)
    return dropped


async def _resolve_planner_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the planning phase.

    Wraps the inherited `create_client` / `get_provider` factories so
    tests can monkey-patch this one function instead of two.
    """
    # Heavy imports deferred to runtime so test_planner_stub.py can
    # mock the SDK surface without forcing the full backend chain
    # at module import time.
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    planning_model = get_phase_model(spec_dir, "planning", None)
    provider_name = infer_provider_from_model(planning_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "planning")
        return create_client(
            project_dir,
            spec_dir,
            planning_model,
            max_thinking_tokens=thinking_budget,
        )
    return get_provider(
        provider_name,
        phase="planning",
        model=planning_model,
        working_dir=project_dir,
        **get_provider_extra_kwargs(provider_name, planning_model),
    )


async def _invoke_session(
    client,
    prompt: str,
    spec_dir: Path,
    verbose: bool,
) -> tuple[str, str, dict]:
    """Thin wrapper around run_agent_session so tests can patch one symbol.

    Returns the (status, response, error_info) triple that
    run_agent_session yields.
    """
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client, prompt, spec_dir, verbose, phase=LogPhase.PLANNING
        )


def _validate_emitted_plan(spec_dir: Path) -> tuple[bool, str, object | None]:
    """Load + validate test_plan.json the agent just wrote.

    Returns ``(ok, error_kind, plan)``:
      - ok=True, error_kind="", plan=ImplementationPlan → valid
      - ok=False, error_kind="missing" → file not written
      - ok=False, error_kind="json"    → file present but invalid JSON
      - ok=False, error_kind="schema"  → JSON valid but doesn't load
        as ImplementationPlan
    """
    from test_plan import ImplementationPlan  # local: avoid SDK cost on import

    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        return False, "missing", None
    try:
        # ImplementationPlan.load reads + parses + builds the dataclass.
        plan = ImplementationPlan.load(plan_file)
    except json.JSONDecodeError as exc:
        return False, "json", str(exc)
    except (KeyError, TypeError, ValueError) as exc:
        return False, "schema", str(exc)
    return True, "", plan


async def run_planner(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "replan"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Planner agent.

    Builds the test-oriented system prompt via ``get_tfactory_planner_prompt``,
    invokes the Claude Agent SDK session via the inherited
    ``run_agent_session`` machinery, then post-validates the emitted
    ``test_plan.json``. Retries once on missing/malformed output with a
    reminder turn before giving up.

    Replan mode (commit 5) is currently a stub that surfaces the
    deferred status and returns False — it'll wire up when the replan
    path lands.

    Args:
        spec_dir: TFactory workspace spec dir
            (``~/.tfactory/workspaces/<project_id>/specs/<spec_id>/``).
        project_dir: AIFactory project root_path. Used by the SDK
            client for Glob/Grep over the diffed code surface.
        mode: 'initial' for first plan, 'replan' for follow-up after
            Gen-Functional rejection (commit 5).
        verbose: forwarded to ``run_agent_session``.

    Returns:
        ``True`` on a valid plan (including ``planned_empty`` — that's
        a warning state, not a failure). ``False`` on hard failure
        (session error, missing file after retry, parse failure after
        retry, malformed workspace).

    Side effects:
        - Updates ``spec_dir/status.json`` (status, phase, planner_*).
        - The SDK agent writes ``spec_dir/test_plan.json`` via its
          Write tool. This function may also write to status.json's
          ``planner_warnings`` list with truncation / soft-fail notes.
    """
    if not spec_dir.is_dir():
        _planner_log.error("planner: spec_dir %s does not exist", spec_dir)
        return False

    if mode == "replan":
        # Commit 5 wires up the real replan path. For now: surface the
        # deferred status and return False so the auto-fire path doesn't
        # silently no-op.
        _write_status_patch(
            spec_dir,
            status="planner_failed",
            phase="planner_replan_not_implemented",
            planner_error="replan mode wires up in commit 5/6 of Task 5",
        )
        return False

    try:
        _write_status_patch(
            spec_dir, status="planning", phase="planner_initial_started"
        )

        # Build the system prompt (loads planner.md + prepends SPEC CONTEXT)
        from prompts_pkg.prompts import get_tfactory_planner_prompt
        prompt = get_tfactory_planner_prompt(spec_dir, project_dir)

        # Resolve the SDK client
        client = await _resolve_planner_client(spec_dir, project_dir)

        # Run the agent session — agent's Write tool emits test_plan.json
        session_status, _response, _err = await _invoke_session(
            client, prompt, spec_dir, verbose
        )
        if session_status == "error":
            _write_status_patch(
                spec_dir,
                status="planner_failed",
                phase="planner_session_error",
                planner_error="run_agent_session returned status=error",
            )
            return False

        # Validate the emitted plan; retry once on missing/malformed.
        ok, err_kind, plan = _validate_emitted_plan(spec_dir)
        if not ok:
            _planner_log.warning(
                "planner: first session produced %s (%s); retrying once",
                err_kind, plan,
            )
            retry_prompt = _build_retry_prompt(
                prompt, err_kind, str(plan or "")[:300]
            )
            client_retry = await _resolve_planner_client(spec_dir, project_dir)
            retry_status, _r, _re = await _invoke_session(
                client_retry, retry_prompt, spec_dir, verbose
            )
            if retry_status == "error":
                _write_status_patch(
                    spec_dir,
                    status="planner_failed",
                    phase="planner_session_error",
                    planner_error="retry session returned status=error",
                )
                return False
            ok, err_kind, plan = _validate_emitted_plan(spec_dir)
            if not ok:
                _write_status_patch(
                    spec_dir,
                    status="planner_failed",
                    phase=f"planner_invalid_{err_kind}_after_retry",
                    planner_error=f"after retry: {err_kind} — {str(plan or '')[:200]}",
                )
                return False

        # plan is now a valid ImplementationPlan instance.
        subtask_count = _count_subtasks(plan)
        warnings: list[str] = []

        if subtask_count > _HARD_SUBTASK_CAP:
            dropped = _truncate_subtasks(plan, _HARD_SUBTASK_CAP)
            warnings.append(
                f"emitted {subtask_count} subtasks; truncated to "
                f"{_HARD_SUBTASK_CAP} (dropped {dropped})"
            )
            plan.save(spec_dir / "test_plan.json")
            subtask_count = _HARD_SUBTASK_CAP
        elif subtask_count > _SOFT_SUBTASK_WARN:
            warnings.append(
                f"emitted {subtask_count} subtasks "
                f"(soft warning above {_SOFT_SUBTASK_WARN})"
            )

        if subtask_count == 0:
            _write_status_patch(
                spec_dir,
                status="planned_empty",
                phase="planner_initial_complete",
                planner_warnings=warnings + [
                    "agent emitted 0 subtasks — downstream pipeline will have nothing to do"
                ],
                subtask_count=0,
            )
            return True

        _write_status_patch(
            spec_dir,
            status="planned",
            phase="planner_initial_complete",
            planner_warnings=warnings,
            subtask_count=subtask_count,
        )
        return True

    except Exception as exc:
        _planner_log.error("planner failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="planner_failed",
            phase=f"planner_{mode}_exception",
            planner_error=str(exc)[:500],
        )
        return False


_RETRY_REMINDERS = {
    "missing": (
        "Your previous turn did not emit `test_plan.json`. "
        "You MUST use the Write tool to create the file at "
        "`{spec_dir}/test_plan.json`. Re-emit the full plan now."
    ),
    "json": (
        "Your previous turn produced `test_plan.json` but it failed to "
        "parse as JSON: {detail}. Re-emit the full plan, double-check "
        "JSON syntax (commas, quotes, brackets) before calling Write."
    ),
    "schema": (
        "Your previous turn produced `test_plan.json` but it didn't "
        "match the ImplementationPlan schema: {detail}. "
        "Re-emit the full plan; pay attention to required Subtask keys: "
        "id, description, status, lane, target, rationale, "
        "files_to_create, verification."
    ),
}


def _build_retry_prompt(
    original_prompt: str, err_kind: str, detail: str
) -> str:
    """Build a retry-turn prompt that re-presents the original system
    prompt + a short corrective note describing what went wrong.
    """
    reminder = _RETRY_REMINDERS.get(
        err_kind,
        "Your previous turn did not produce a valid test_plan.json. Re-emit.",
    ).format(spec_dir="<workspace>", detail=detail)
    return (
        f"## RETRY ({err_kind})\n\n"
        f"{reminder}\n\n"
        f"---\n\n"
        f"{original_prompt}"
    )


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
