"""Plan-approval endpoints — extracted from routes/tasks.py (#360 god-file split).

A focused sub-router for the plan review decisions, carved out of
routes/tasks.py. Behaviour and paths unchanged; main.py mounts it under the
same /api/tasks prefix. Shared helpers/models still live in routes/tasks.py and
are imported here.

    POST /api/tasks/{task_id}/approve-plan
    POST /api/tasks/{task_id}/reject-plan
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

from ._specpath import safe_spec_dir
from .projects import load_projects
from .tasks import ApprovePlanRequest, RejectPlanRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/{task_id}/approve-plan")
async def approve_plan(
    task_id: str, request: ApprovePlanRequest = ApprovePlanRequest()
):
    """Approve a task's plan to allow coding to proceed.

    When a task is in plan_review status (waiting for human approval),
    this endpoint marks the plan as approved and optionally restarts the task.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = task_id.split(":", 1)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = safe_spec_dir(project_path, spec_id)

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Import ReviewState from backend
    import sys

    backend_path = Path(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    from review import ReviewState

    # Approve the plan
    review_state = ReviewState.load(spec_dir)
    review_state.approve(spec_dir, approved_by="web_user")

    # Update test_plan.json status back to in_progress
    plan_file = spec_dir / "test_plan.json"
    plan_updated = False
    if plan_file.exists():
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info(f"[ApprovePlan] Reading plan file: {plan_file}")
            plan = json.loads(plan_file.read_text())
            logger.info(
                f"[ApprovePlan] Current status: {plan.get('status')}, planStatus: {plan.get('planStatus')}, reviewReason: {plan.get('reviewReason')}"
            )

            # Update BOTH status and planStatus fields
            plan["status"] = "in_progress"
            plan["planStatus"] = "in_progress"
            plan.pop("reviewReason", None)

            plan_file.write_text(json.dumps(plan, indent=2))
            plan_updated = True
            logger.info(
                "[ApprovePlan] Updated plan file - status: in_progress, planStatus: in_progress"
            )
        except (json.JSONDecodeError, OSError) as e:
            import logging

            logging.getLogger(__name__).error(
                f"[ApprovePlan] Failed to update plan file: {e}"
            )
    else:
        import logging

        logging.getLogger(__name__).warning(
            f"[ApprovePlan] Plan file does not exist: {plan_file}"
        )

    # Emit status change via WebSocket
    from ..websockets.events import emit_task_status

    await emit_task_status(task_id, "in_progress")

    auto_restarted = False

    # Auto-restart if requested
    if request.auto_restart:
        try:
            from ..services.agent_service import get_agent_service

            agent_service = get_agent_service()

            # Clean up stale spec creation process if still tracked as running.
            # The spec_runner process may have exited but the monitor may not have
            # cleaned up running_tasks (e.g., if the process hung or monitor failed).
            if agent_service.is_running(task_id):
                import logging

                logger = logging.getLogger(__name__)
                logger.info(
                    f"[ApprovePlan] Cleaning up stale spec creation process for {task_id}"
                )
                try:
                    await agent_service.stop_task(task_id)
                except Exception as stop_err:
                    logger.warning(
                        f"[ApprovePlan] Failed to stop stale process: {stop_err}"
                    )
                    # Force-remove from running_tasks as fallback
                    agent_service.running_tasks.pop(task_id, None)

            # Read mode from task_metadata.json
            task_metadata_file = spec_dir / "task_metadata.json"
            mode = "full"
            if task_metadata_file.exists():
                try:
                    metadata = json.loads(task_metadata_file.read_text())
                    mode = metadata.get("mode", "full")
                except (json.JSONDecodeError, OSError):
                    pass

            await agent_service.start_task_execution(
                task_id=task_id,
                project_path=project_path,
                spec_id=spec_id,
                auto_continue=True,
                mode=mode,
                force=True,  # Bypass approval check since plan was manually approved
            )
            auto_restarted = True
        except Exception as e:
            # If auto-restart fails, still return success for approval
            import logging

            logging.getLogger(__name__).warning(
                f"Auto-restart failed for {task_id}: {e}"
            )

    return {
        "success": True,
        "task_id": task_id,
        "message": "Plan approved" + (" and task restarted" if auto_restarted else ""),
        "autoRestarted": auto_restarted,
    }


@router.post("/{task_id}/reject-plan")
async def reject_plan(task_id: str, request: RejectPlanRequest = RejectPlanRequest()):
    """Reject a task's plan and send the planner back to iterate.

    Used by the human-review checkpoint when the implementation plan needs
    rework. The optional ``feedback`` field is appended to the spec's
    review-state feedback log so the planner's next pass picks it up.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid task ID format"
        )

    project_id, spec_id = task_id.split(":", 1)
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = safe_spec_dir(project_path, spec_id)
    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Import ReviewState the same way approve_plan does (sys.path shim
    # because the web-server doesn't have ``backend`` on its PYTHONPATH
    # in every install layout).
    import sys

    backend_path = Path(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    from review.state import ReviewState

    review_state = ReviewState.load(spec_dir)
    review_state.reject(spec_dir)
    if request.feedback:
        review_state.add_feedback(request.feedback, spec_dir=spec_dir)

    # Mirror approve_plan's bookkeeping: flip the plan back to "needs work"
    # so the next planner pass sees a clean slate.
    plan_file = spec_dir / "test_plan.json"
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
            plan["status"] = "rejected"
            plan["planStatus"] = "rejected"
            if request.feedback:
                plan["reviewReason"] = request.feedback
            plan_file.write_text(json.dumps(plan, indent=2))
        except (OSError, json.JSONDecodeError) as exc:
            # Plan file unreadable — review state was already updated, so
            # the reject took effect even if the bookkeeping fails. Log
            # and continue.
            import logging

            logging.getLogger(__name__).warning(
                f"[RejectPlan] couldn't update test_plan.json: {exc}"
            )

    return {
        "success": True,
        "task_id": task_id,
        "feedback_recorded": bool(request.feedback),
    }
