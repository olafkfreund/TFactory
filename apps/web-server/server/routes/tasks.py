"""
Task management routes.

Handles CRUD operations for tasks (specs) within projects.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ._specpath import _validate_component, safe_spec_dir
from .projects import load_projects

router = APIRouter()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


# Frontend-compatible task statuses (matches frontend KanbanBoard columns)
TaskStatus = Literal[
    "backlog",
    "in_progress",
    "ai_review",
    "human_review",
    "done",
]

# Backend statuses that get mapped to frontend statuses:
# backlog -> backlog           (not started)
# planning -> backlog          (still in queue)
# in_progress -> in_progress   (actively building)
# review -> human_review       (build finished, needs merge review)
# qa_pending -> ai_review      (QA running)
# qa_failed -> human_review    (QA failed, needs human attention)
# completed -> human_review    (finished, needs final approval/merge)
# cancelled -> backlog         (cancelled, shown in backlog)


class SubtaskVerification(BaseModel):
    """Verification configuration for a subtask."""

    type: str = "command"  # Verification type (e.g., "command", "browser", "manual", "code_review", "testing", etc.)
    run: str | None = None  # Command to run (e.g., "npm test")
    scenario: str | None = None  # Browser test scenario


class Subtask(BaseModel):
    """Subtask model from implementation plan."""

    id: str
    title: str
    description: str | None = None
    status: str = "pending"
    files: list[str] = Field(default_factory=list)  # Files affected by this subtask
    verification: SubtaskVerification | None = None  # How to verify completion
    # Lane + timing for the cockpit's live execution diagram (#94). The test
    # plan tags each subtask with a lane (unit/browser/api/integration/mutation);
    # the cockpit aggregates them into a lane pipeline. Additive + optional.
    lane: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class TaskBase(BaseModel):
    """Base task model."""

    title: str = Field(..., description="Task title")
    description: str = Field(..., description="Task description/requirements")


class TaskCreate(TaskBase):
    """Model for creating a new task."""

    project_id: str = Field(..., description="ID of the project this task belongs to")
    metadata: Optional["TaskMetadataUpdate"] = Field(
        None, description="Optional task metadata"
    )


class SelectedSkill(BaseModel):
    """A skill selected to be applied to a task."""

    id: str  # '{category}/{skill_name}'
    name: str  # human-readable display name
    category: str  # parent category
    source: str | None = None  # optional source URL from skill metadata


class TaskMetadata(BaseModel):
    """Task metadata fields."""

    sourceType: str | None = None
    category: str | None = None
    priority: str | None = None
    complexity: str | None = None
    impact: str | None = None
    # GitHub integration
    githubIssueNumber: int | None = None
    affectedFiles: list[str] | None = None
    acceptanceCriteria: list[str] | None = None
    model: str | None = None
    thinkingLevel: str | None = None
    requireReviewBeforeCoding: bool | None = None
    # Execution mode: 'quick' uses simplified prompts (~70% fewer tokens)
    mode: str | None = None  # 'quick' or 'full'
    # Phase-specific model/thinking configuration (Auto profile)
    isAutoProfile: bool | None = None
    phaseModels: dict | None = None
    phaseThinking: dict | None = None
    # Git options
    baseBranch: str | None = None
    # Archive info
    archivedAt: str | None = None
    archivedInVersion: str | None = None
    # Skills attached to this task
    selectedSkills: list[SelectedSkill] | None = None


class Task(TaskBase):
    """Full task model with all fields."""

    id: str = Field(..., description="Unique task ID")
    spec_id: str = Field(..., description="Spec directory name (e.g., '001-feature')")
    project_id: str = Field(..., description="Project ID")
    status: TaskStatus = Field("backlog", description="Current task status")
    phase: str | None = Field(None, description="Current execution phase")
    subtasks: list[Subtask] = Field(default_factory=list)
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")
    worktree_path: str | None = Field(
        None, description="Path to git worktree if active"
    )
    branch_name: str | None = Field(None, description="Git branch name")
    metadata: TaskMetadata | None = Field(None, description="Task metadata")
    review_reason: str | None = Field(
        None, description="Reason for human review (e.g., 'plan_review')"
    )


class TaskList(BaseModel):
    """Response model for listing tasks."""

    tasks: list[Task]
    total: int


class TaskMetadataUpdate(BaseModel):
    """Model for updating task metadata fields.

    Fields can be set to None to explicitly clear them from the task.
    When a field is not provided (excluded from the request), it won't be modified.
    When a field is set to null/None, it will be removed from the task metadata.
    """

    model: str | None = None
    thinkingLevel: str | None = None
    requireReviewBeforeCoding: bool | None = None
    category: str | None = None
    priority: str | None = None
    complexity: str | None = None
    impact: str | None = None
    # Phase-specific model/thinking configuration (Auto profile)
    isAutoProfile: bool | None = None
    phaseModels: dict | None = None  # {"spec": "sonnet", "planning": "opus", ...}
    phaseThinking: dict | None = None  # {"spec": "medium", "planning": "high", ...}
    # Git options
    baseBranch: str | None = None
    # Execution mode: 'quick' uses simplified prompts (~70% fewer tokens)
    mode: str | None = None  # 'quick' or 'full'
    # Image attachments (can be null to clear)
    attachedImages: list | None = None
    # Referenced files (can be null to clear)
    referencedFiles: list | None = None
    # Skills attached to this task (can be null to clear)
    selectedSkills: list[SelectedSkill] | None = None


class TaskUpdate(BaseModel):
    """Model for updating task fields."""

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    metadata: TaskMetadataUpdate | None = None


class ClarificationQuestion(BaseModel):
    """A single clarification question with multiple-choice options."""

    id: str
    question: str
    options: list[str] = Field(default_factory=list)


class ClarificationResponse(BaseModel):
    """Response from clarification question generation."""

    questions: list[ClarificationQuestion] = Field(default_factory=list)
    skip: bool = False
    skip_reason: str = Field("", alias="skipReason")

    model_config = {"populate_by_name": True}


class ClarificationAnswer(BaseModel):
    """A single answered clarification question."""

    question_id: str = Field(..., alias="questionId")
    question: str
    answer: str

    model_config = {"populate_by_name": True}


class ClarificationAnswersRequest(BaseModel):
    """Request to submit clarification answers."""

    answers: list[ClarificationAnswer]


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------


def get_spec_dirs(project_path: Path) -> list[Path]:
    """Get all spec directories in a project."""
    specs_dir = project_path / ".tfactory" / "specs"
    if not specs_dir.exists():
        return []
    return sorted([d for d in specs_dir.iterdir() if d.is_dir()])


def get_next_spec_id(project_path: Path, title: str) -> str:
    """Generate the next spec ID (e.g., '003-feature-name').

    Uses a counter file (.tfactory/specs/.counter) to ensure IDs
    never get reused after deletion.
    """
    specs_dir = project_path / ".tfactory" / "specs"
    counter_file = specs_dir / ".counter"

    # Read persisted counter (highest ID ever assigned)
    persisted_max = 0
    if counter_file.exists():
        try:
            persisted_max = int(counter_file.read_text().strip())
        except (ValueError, OSError):
            pass

    # Also check existing directories in case counter file is missing
    existing = get_spec_dirs(project_path)
    dir_max = 0
    for spec_dir in existing:
        match = re.match(r"(\d+)-", spec_dir.name)
        if match:
            dir_max = max(dir_max, int(match.group(1)))

    # Use the higher of persisted counter and directory scan
    max_num = max(persisted_max, dir_max)
    next_num = max_num + 1

    # Persist the new counter
    specs_dir.mkdir(parents=True, exist_ok=True)
    counter_file.write_text(str(next_num))

    # Generate slug from title
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30]

    # Fallback to "untitled-task" if slug is empty
    if not slug:
        slug = "untitled-task"

    return f"{next_num:03d}-{slug}"


def get_worktree_spec_dir(project_path: Path, spec_id: str) -> Path | None:
    """Get the worktree spec directory if it exists.

    Worktree layout: .tfactory/worktrees/tasks/{spec_id}/.tfactory/specs/{spec_id}/
    """
    worktree_spec_dir = (
        project_path
        / ".tfactory"
        / "worktrees"
        / "tasks"
        / spec_id
        / ".tfactory"
        / "specs"
        / spec_id
    )
    if worktree_spec_dir.exists():
        return worktree_spec_dir
    return None


def sync_worktree_to_main_spec(project_path: Path, spec_id: str) -> bool:
    """Sync test_plan.json from worktree to main spec if worktree has newer data.

    Returns True if sync was performed, False otherwise.
    """
    main_spec_dir = safe_spec_dir(project_path, spec_id)
    worktree_spec_dir = get_worktree_spec_dir(project_path, spec_id)

    if not worktree_spec_dir:
        return False

    worktree_plan_file = worktree_spec_dir / "test_plan.json"
    main_plan_file = main_spec_dir / "test_plan.json"

    if not worktree_plan_file.exists():
        return False

    try:
        worktree_plan = json.loads(worktree_plan_file.read_text())
        main_plan = {}
        if main_plan_file.exists():
            main_plan = json.loads(main_plan_file.read_text())

        # Count completed subtasks in each plan
        def count_completed(plan: dict) -> int:
            count = 0
            for phase in plan.get("phases", []):
                for subtask in phase.get("subtasks", []):
                    if subtask.get("status") == "completed":
                        count += 1
            return count

        worktree_completed = count_completed(worktree_plan)
        main_completed = count_completed(main_plan)

        # Only sync if worktree has more progress (more completed subtasks)
        if worktree_completed > main_completed:
            import logging

            logger = logging.getLogger(__name__)
            logger.info(
                f"[WorktreeSync] Syncing plan for {spec_id}: "
                f"worktree has {worktree_completed} completed vs main {main_completed}"
            )
            main_plan_file.write_text(json.dumps(worktree_plan, indent=2))
            return True

        return False
    except (json.JSONDecodeError, OSError) as e:
        import logging

        logging.getLogger(__name__).warning(
            f"[WorktreeSync] Failed to sync {spec_id}: {e}"
        )
        return False


def validate_done_status(plan: dict) -> tuple[bool, str]:
    """Validate that all subtasks are completed before allowing 'done' status.

    Returns (is_valid, error_message).
    """
    phases = plan.get("phases", [])
    if not phases:
        # No phases means no subtasks to validate
        return True, ""

    total_subtasks = 0
    completed_subtasks = 0

    for phase in phases:
        for subtask in phase.get("subtasks", []):
            total_subtasks += 1
            if subtask.get("status") == "completed":
                completed_subtasks += 1

    if total_subtasks == 0:
        return True, ""

    if completed_subtasks < total_subtasks:
        return False, (
            f"Cannot mark as done: only {completed_subtasks}/{total_subtasks} "
            f"subtasks are completed. Complete all subtasks first or check if "
            f"worktree has newer progress."
        )

    return True, ""


def get_plan_with_worktree_sync(project_path: Path, spec_id: str) -> tuple[dict, Path]:
    """Get implementation plan, syncing from worktree first if needed.

    Returns (plan_dict, plan_file_path).
    """
    # Sync worktree to main spec first
    sync_worktree_to_main_spec(project_path, spec_id)

    # Read from main spec (now potentially updated)
    main_spec_dir = safe_spec_dir(project_path, spec_id)
    plan_file = main_spec_dir / "test_plan.json"

    plan = {}
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
        except json.JSONDecodeError:
            pass

    return plan, plan_file


def _resolve_correlation_issue(spec_dir: Path) -> int | None:
    """Resolve the GitHub issue number that ties this spec to its upstream
    AIFactory/PFactory work item — the PARR correlation key the CFactory cockpit
    uses to attach a TFactory task to its issue-keyed work item (#94).

    The key is stored per spec but only the handback path read it; the task list
    never surfaced it, so the cockpit fell back to the spec id, never matched the
    work item, and the test-stage lane stayed empty. Mirror the handback
    precedence (RFC-0002 contract -> source.json) and return the issue as an int,
    or None when absent / non-numeric. Best-effort; never raises.
    """
    ctx = spec_dir / "context"

    def _load(path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    candidates: list = []
    # RFC-0002 contract drops carry the correlation key directly.
    for name in ("task_contract.json", "aifactory_plan.json"):
        candidates.append(_load(ctx / name).get("correlation_key"))
    # source.json: a top-level key, an embedded contract, or the raw issue.
    source = _load(ctx / "source.json")
    embedded = source.get("contract") or source.get("task_contract")
    if isinstance(embedded, dict):
        candidates.append(embedded.get("correlation_key"))
    candidates += [
        source.get("correlation_key"),
        source.get("issue_number"),
        source.get("correlation_id"),
    ]

    for value in candidates:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def load_spec_metadata(spec_dir: Path) -> dict:
    """Load metadata for a spec from its files."""
    metadata = {
        "title": spec_dir.name,
        "description": "",
        "status": "backlog",
        "phase": None,
        "subtasks": [],
        "worktree_path": None,
        "branch_name": None,
        "archivedAt": None,
        "archivedInVersion": None,
        "reviewReason": None,
    }

    # Try to load requirements.json for title/description (most accurate source)
    requirements_file = spec_dir / "requirements.json"
    if requirements_file.exists():
        try:
            requirements = json.loads(requirements_file.read_text())
            if "title" in requirements:
                metadata["title"] = requirements["title"]
            if "description" in requirements:
                metadata["description"] = requirements["description"]
        except (json.JSONDecodeError, KeyError):
            pass

    # Fall back to spec.md if requirements.json not available
    if not metadata["description"]:
        spec_file = spec_dir / "spec.md"
        if spec_file.exists():
            content = spec_file.read_text()
            # Extract title from first # heading if not already set
            if not metadata["title"] or metadata["title"] == spec_dir.name:
                title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                if title_match:
                    metadata["title"] = title_match.group(1)
            # Use first paragraph as description (no truncation)
            paragraphs = re.split(r"\n\n+", content)
            for p in paragraphs[1:]:  # Skip title
                if p.strip() and not p.startswith("#"):
                    metadata["description"] = p.strip()
                    break

    # Try to load task_logs.json for active phase status (most accurate)
    task_logs_file = spec_dir / "task_logs.json"
    if task_logs_file.exists():
        try:
            logs = json.loads(task_logs_file.read_text())
            phases = logs.get("phases", {})

            # First check for any active phase
            has_active_phase = False
            for phase_name, phase_data in phases.items():
                if phase_data.get("status") == "active":
                    metadata["phase"] = phase_name
                    metadata["status"] = "in_progress"
                    has_active_phase = True
                    break

            # If no active phase, check for terminal states
            if not has_active_phase:
                # Check if any phase failed → task needs human intervention
                has_failed_phase = any(
                    phase_data.get("status") == "failed"
                    for phase_data in phases.values()
                )
                if has_failed_phase:
                    metadata["status"] = "human_review"
                    metadata["reviewReason"] = "errors"
                else:
                    # Check validation phase completed (strongest completion signal)
                    validation_phase = phases.get("validation", {})
                    if validation_phase.get(
                        "status"
                    ) == "completed" and validation_phase.get("entries"):
                        metadata["phase"] = "validation"
                        metadata["status"] = "human_review"
                        metadata["reviewReason"] = "completed"
                    else:
                        # Fall back to coding phase completed
                        coding_phase = phases.get("coding", {})
                        if coding_phase.get(
                            "status"
                        ) == "completed" and coding_phase.get("entries"):
                            metadata["phase"] = "coding"
                            metadata["status"] = "human_review"
                            metadata["reviewReason"] = "completed"
        except (json.JSONDecodeError, KeyError):
            pass

    # Try to load test_plan.json for status/subtasks
    plan_file = spec_dir / "test_plan.json"
    explicit_status = None  # Track if user explicitly set status via kanban
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
            # Only set phase from plan if not already set from task_logs
            if not metadata["phase"]:
                metadata["phase"] = plan.get("phase")

            # If no explicit phase, try to detect from phases array
            if not metadata["phase"] and "phases" in plan:
                for phase in plan["phases"]:
                    if isinstance(phase, dict):
                        phase_status = phase.get("status", "")
                        if phase_status == "in_progress":
                            metadata["phase"] = phase.get("name", phase.get("id"))
                            break

            # Check if status was explicitly set (kanban drag-drop saves this)
            # "done" and "completed" statuses ALWAYS take precedence (task was explicitly finished)
            # Other statuses only apply if we didn't already detect active status from task_logs
            if "status" in plan:
                explicit_status = plan["status"]
                if explicit_status in ("done", "completed"):
                    # Task was explicitly marked as done - always honor this
                    metadata["status"] = explicit_status
                elif metadata["status"] == "backlog":
                    # Only override backlog with other statuses
                    metadata["status"] = explicit_status

            # Load reviewReason if present (e.g., 'plan_review')
            if "reviewReason" in plan:
                metadata["reviewReason"] = plan["reviewReason"]

            # Check for qa_signoff.status == "approved" which means task completed QA
            # This should show as human_review for final merge approval
            qa_signoff = plan.get("qa_signoff") or {}
            if (
                qa_signoff.get("status") == "approved"
                and metadata["status"] == "backlog"
            ):
                metadata["status"] = "human_review"
                metadata["reviewReason"] = "completed"

            # Load archive metadata
            if "archivedAt" in plan:
                metadata["archivedAt"] = plan["archivedAt"]
            if "archivedInVersion" in plan:
                metadata["archivedInVersion"] = plan["archivedInVersion"]

            # Load subtasks - can be at top level or nested in phases
            all_subtasks = []

            # First check for top-level subtasks (legacy format).
            # Tolerate both list shape (canonical) and dict shape
            # (partial-sync artifact from agent_service that maps
            # subtask_id -> {status, notes, ...}).  Without this guard,
            # iterating a dict yields the keys as strings and the loop
            # at the bottom blows up with AttributeError on st.get(...).
            if "subtasks" in plan:
                raw_subtasks = plan["subtasks"]
                if isinstance(raw_subtasks, list):
                    all_subtasks.extend(raw_subtasks)
                elif isinstance(raw_subtasks, dict):
                    for sid, st in raw_subtasks.items():
                        if isinstance(st, dict):
                            st_copy = dict(st)
                            st_copy.setdefault("id", sid)
                            all_subtasks.append(st_copy)

            # Then check for subtasks nested in phases (current format)
            if "phases" in plan:
                for phase in plan["phases"]:
                    if isinstance(phase, dict) and "subtasks" in phase:
                        phase_name = phase.get("name", "")
                        for st in phase["subtasks"]:
                            # Prefix subtask with phase name for clarity
                            st_copy = st.copy() if isinstance(st, dict) else {}
                            if phase_name and "title" not in st_copy:
                                st_copy["title"] = st_copy.get("description", "Subtask")
                            all_subtasks.append(st_copy)

            if all_subtasks:
                metadata["subtasks"] = []
                for i, st in enumerate(all_subtasks):
                    # Build files list from 'file' (single) or 'files'
                    # (array) fields.  Tolerate three shapes the planner
                    # has been observed to emit:
                    #   files: "path/to/x.py"                  (str)
                    #   files: ["a.py", "b.py"]                (list[str])
                    #   files: {"create": ["a.py"], "modify": ["b.py"]}
                    #     (dict — happens when the planner groups files
                    #     by intent; flatten the values into a single
                    #     list of strings)
                    files = []
                    if st.get("file"):
                        files.append(st["file"])
                    raw_files = st.get("files")
                    if isinstance(raw_files, str):
                        files.append(raw_files)
                    elif isinstance(raw_files, list):
                        files.extend(f for f in raw_files if isinstance(f, str))
                    elif isinstance(raw_files, dict):
                        for v in raw_files.values():
                            if isinstance(v, list):
                                files.extend(f for f in v if isinstance(f, str))
                            elif isinstance(v, str):
                                files.append(v)

                    # Build verification from 'verification' or 'verification_method' fields
                    verification = None
                    if st.get("verification"):
                        v = st["verification"]
                        if isinstance(v, dict):
                            verification = SubtaskVerification(
                                type=v.get("type", "command"),
                                run=v.get("run") or v.get("command"),
                                scenario=v.get("scenario"),
                            )
                        elif isinstance(v, str):
                            # Simple string verification becomes a command
                            verification = SubtaskVerification(type="command", run=v)
                    elif st.get("verification_method"):
                        verification = SubtaskVerification(
                            type="command", run=st["verification_method"]
                        )

                    metadata["subtasks"].append(
                        Subtask(
                            id=st.get("id", str(i)),
                            title=st.get("title")
                            or st.get("description", f"Subtask {i + 1}")[:80],
                            description=st.get("description") or st.get("notes"),
                            status=st.get("status", "pending"),
                            files=files,
                            verification=verification,
                            # Lane + timing for the live diagram (#94). Present on
                            # lane-tagged test plans; tolerate absence.
                            lane=st.get("lane"),
                            started_at=st.get("started_at"),
                            completed_at=st.get("completed_at"),
                        )
                    )
        except (json.JSONDecodeError, KeyError):
            pass

    # Check for worktree
    worktree_marker = spec_dir / ".worktree_path"
    if worktree_marker.exists():
        metadata["worktree_path"] = worktree_marker.read_text().strip()
        metadata["branch_name"] = f"tfactory/{spec_dir.name}"

    # Load task metadata from requirements.json
    requirements_file = spec_dir / "requirements.json"
    if requirements_file.exists():
        try:
            requirements = json.loads(requirements_file.read_text())
            metadata["task_metadata"] = requirements.get("metadata", {})
        except (json.JSONDecodeError, KeyError):
            metadata["task_metadata"] = {}
    else:
        metadata["task_metadata"] = {}

    # Surface the PARR correlation key (GitHub issue number) so the CFactory
    # cockpit can attach this TFactory task to its issue-keyed work item and
    # render the test-stage lane (#94). Stored per spec (RFC-0002 contract /
    # source.json) but never exposed on the task-list row, so the test lane
    # showed empty even when verification was running.
    if not metadata["task_metadata"].get("githubIssueNumber"):
        issue = _resolve_correlation_issue(spec_dir)
        if issue is not None:
            metadata["task_metadata"]["githubIssueNumber"] = issue

    # Detect status from subtask progress if not already set
    # If any subtasks are completed but not all done, task is in_progress
    if metadata["status"] == "backlog" and metadata.get("subtasks"):
        subtasks = metadata["subtasks"]
        completed_count = sum(1 for st in subtasks if st.status == "completed")
        in_progress_count = sum(1 for st in subtasks if st.status == "in_progress")
        if completed_count > 0 and completed_count < len(subtasks):
            # Work has been done but not finished
            metadata["status"] = "in_progress"
            metadata["phase"] = "coding"
        elif in_progress_count > 0:
            # Currently working on subtasks
            metadata["status"] = "in_progress"
            metadata["phase"] = "coding"
        elif completed_count == len(subtasks) and len(subtasks) > 0:
            # All subtasks completed - needs review
            metadata["status"] = "human_review"
            metadata["reviewReason"] = "completed"

    # Final safety: "done"/"completed" always wins over all auto-detection
    # This guards against task_logs or subtask detection overriding user intent
    if explicit_status in ("done", "completed"):
        metadata["status"] = explicit_status

    # Only use file-based status detection if no explicit status was set via kanban
    # AND status wasn't already determined from task_logs.json (coding completed)
    # This allows users to override status via drag-and-drop
    if explicit_status is None and metadata["status"] == "backlog":
        if (spec_dir / "QA_FIX_REQUEST.md").exists():
            metadata["status"] = "human_review"
            metadata["reviewReason"] = "qa_rejected"
        elif (spec_dir / "qa_report.md").exists():
            report = (spec_dir / "qa_report.md").read_text()
            if "PASSED" in report.upper():
                metadata["status"] = "human_review"
                metadata["reviewReason"] = "completed"
            elif "FAILED" in report.upper():
                metadata["status"] = "human_review"
                metadata["reviewReason"] = "qa_rejected"
            else:
                metadata["status"] = "ai_review"  # QA still in progress
        elif metadata["phase"]:
            metadata["status"] = "in_progress"

    return metadata


def spec_to_task(project_id: str, spec_dir: Path) -> Task:
    """Convert a spec directory to a Task model."""
    metadata = load_spec_metadata(spec_dir)

    # Get timestamps from directory
    stat = spec_dir.stat()

    # Map backend status to frontend-compatible status
    frontend_status = map_backend_status_to_frontend(metadata["status"])

    # Build task metadata if available
    task_metadata = None
    if metadata.get("task_metadata"):
        task_metadata = TaskMetadata(**metadata["task_metadata"])

    return Task(
        id=f"{project_id}:{spec_dir.name}",
        spec_id=spec_dir.name,
        project_id=project_id,
        title=metadata["title"],
        description=metadata["description"],
        status=frontend_status,
        phase=metadata["phase"],
        subtasks=metadata["subtasks"],
        created_at=datetime.fromtimestamp(stat.st_ctime).isoformat(),
        updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        worktree_path=metadata["worktree_path"],
        branch_name=metadata["branch_name"],
        metadata=task_metadata,
        review_reason=metadata.get("reviewReason"),
    )


def map_backend_status_to_frontend(backend_status: str) -> str:
    """Map backend task status to frontend-compatible status.

    Backend statuses: backlog, planning, in_progress, review, qa_pending, qa_failed, completed, cancelled
    Frontend statuses: backlog, in_progress, ai_review, human_review, done
    """
    status_mapping = {
        # Backend statuses -> frontend statuses
        "backlog": "backlog",
        "planning": "backlog",  # Planning tasks go in backlog column
        "in_progress": "in_progress",
        "review": "human_review",  # Build ready for review/merge - needs human action
        "qa_pending": "ai_review",
        "qa_failed": "human_review",  # Failed QA needs human attention
        "completed": "human_review",  # Completed tasks need merge approval
        "cancelled": "backlog",  # Cancelled tasks shown in backlog (could be hidden later)
        # Frontend statuses (pass through when already mapped or set via kanban)
        "ai_review": "ai_review",
        "human_review": "human_review",
        "done": "done",
    }
    return status_mapping.get(backend_status, "backlog")


def get_execution_progress(spec_dir: Path, subtasks: list) -> dict | None:
    """Compute execution progress from task_logs.json and subtasks.

    Returns ExecutionProgress dict or None if not available.
    """
    # Also check worktree for task_logs.json
    project_path = spec_dir.parent.parent  # .tfactory/specs -> project root
    worktree_spec_dir = (
        project_path
        / "worktrees"
        / "tasks"
        / spec_dir.name
        / ".tfactory"
        / "specs"
        / spec_dir.name
    )

    task_logs_file = None
    for check_dir in [worktree_spec_dir, spec_dir]:
        candidate = check_dir / "task_logs.json"
        if candidate.exists():
            task_logs_file = candidate
            break

    if not task_logs_file:
        return None

    try:
        task_logs = json.loads(task_logs_file.read_text())
        phases = task_logs.get("phases", {})

        # Determine current phase from task_logs status
        # Maps task_logs.json phase names to frontend ExecutionPhase values
        phase_map = {
            "planning": "planning",
            "plan_review": "plan_review",
            "coding": "coding",
            "validation": "qa_review",
            "qa_review": "qa_review",
            "qa_fixing": "qa_fixing",
            "complete": "complete",
            "failed": "failed",
        }

        # Phase order for progress calculation
        phase_order = ["planning", "plan_review", "coding", "validation", "qa_fixing"]
        phase_weights = {
            "planning": 10,
            "plan_review": 5,
            "coding": 60,
            "validation": 15,
            "qa_fixing": 10,
        }  # % of total progress

        current_phase = "idle"
        current_phase_key = None
        started_at = None
        phase_progress = 0

        for log_phase, log_data in phases.items():
            # Get earliest started_at from any phase
            if log_data.get("started_at") and not started_at:
                started_at = log_data["started_at"]
            elif log_data.get("started_at") and started_at:
                # Keep the earliest timestamp
                if log_data["started_at"] < started_at:
                    started_at = log_data["started_at"]

            if log_data.get("status") == "active":
                current_phase = phase_map.get(log_phase, log_phase)
                current_phase_key = log_phase

        # If no active phase, check for terminal states (completed/failed)
        if current_phase == "idle" and phases:
            has_failed = any(p.get("status") == "failed" for p in phases.values())
            has_completed = any(p.get("status") == "completed" for p in phases.values())

            if has_failed:
                current_phase = "failed"
            elif has_completed:
                validation = phases.get("validation", {})
                coding = phases.get("coding", {})
                if validation.get("status") == "completed":
                    current_phase = "complete"
                elif coding.get("status") == "completed":
                    current_phase = "complete"

        # Calculate overall progress from subtasks
        completed = sum(1 for s in subtasks if s.status == "completed")
        total = len(subtasks)
        overall_progress = int((completed / total) * 100) if total > 0 else 0

        # Override progress for terminal states
        if current_phase in ("complete", "failed"):
            phase_progress = 100
            overall_progress = 100

        # Calculate phase-specific progress
        if current_phase_key:
            phase_data = phases.get(current_phase_key, {})
            entries = phase_data.get("entries", [])
            # Estimate phase progress based on entries (simple heuristic)
            if entries:
                # Count completed tools vs total activity
                tool_starts = sum(1 for e in entries if e.get("type") == "tool_start")
                tool_ends = sum(1 for e in entries if e.get("type") == "tool_end")
                if tool_starts > 0:
                    phase_progress = min(100, int((tool_ends / tool_starts) * 100))
                else:
                    phase_progress = 50  # Activity detected but no tools tracked
            else:
                phase_progress = 10  # Phase started but no entries yet

        # Find current subtask
        current_subtask = None
        for s in subtasks:
            if s.status == "in_progress":
                current_subtask = s.title
                break

        # Generate sequence number from file modification time for stale update detection
        sequence_number = int(task_logs_file.stat().st_mtime * 1000)

        return {
            "phase": current_phase,
            "phaseProgress": phase_progress,
            "overallProgress": overall_progress,
            "currentSubtask": current_subtask,
            "message": f"{completed}/{total} subtasks completed",
            "startedAt": started_at,
            "sequenceNumber": sequence_number,
        }
    except (json.JSONDecodeError, Exception):
        return None


def task_to_dict(task: Task) -> dict:
    """Convert a Task model to a dict with camelCase keys for frontend."""
    # Get execution progress and archive metadata if task has a spec directory
    execution_progress = None
    archive_metadata = {}
    specs_path = None
    if task.spec_id:
        # Try to find spec dir for this task
        projects = load_projects()
        if task.project_id in projects:
            project_path = Path(projects[task.project_id]["path"])
            spec_dir = project_path / ".tfactory" / "specs" / task.spec_id
            if spec_dir.exists():
                specs_path = str(spec_dir)  # Store path for frontend Files tab
                execution_progress = get_execution_progress(spec_dir, task.subtasks)
                # Load archive metadata from plan file
                plan_file = spec_dir / "test_plan.json"
                if plan_file.exists():
                    try:
                        plan = json.loads(plan_file.read_text())
                        if "archivedAt" in plan:
                            archive_metadata["archivedAt"] = plan["archivedAt"]
                        if "archivedInVersion" in plan:
                            archive_metadata["archivedInVersion"] = plan[
                                "archivedInVersion"
                            ]
                    except json.JSONDecodeError:
                        pass

    result = {
        "id": task.id,
        "specId": task.spec_id,
        "projectId": task.project_id,
        "title": task.title,
        "description": task.description,
        "status": map_backend_status_to_frontend(task.status),
        "phase": task.phase,
        "subtasks": [
            {
                "id": s.id,
                "title": s.title,
                "description": s.description,
                "status": s.status,
                "files": s.files,
                "verification": {
                    "type": s.verification.type,
                    "run": s.verification.run,
                    "scenario": s.verification.scenario,
                }
                if s.verification
                else None,
                # Lane + timing for the cockpit's live diagram (#94).
                "lane": getattr(s, "lane", None),
                "started_at": getattr(s, "started_at", None),
                "completed_at": getattr(s, "completed_at", None),
            }
            for s in task.subtasks
        ],
        "logs": [],  # Required by frontend Task interface
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "worktreePath": task.worktree_path,
        "branchName": task.branch_name,
        "reviewReason": task.review_reason,
        "specsPath": specs_path,  # Path to spec directory for Files tab
    }

    if execution_progress:
        result["executionProgress"] = execution_progress

    # Include task metadata (settings from requirements.json)
    metadata_payload = (
        task.metadata.model_dump(exclude_none=True) if task.metadata else {}
    )
    if archive_metadata:
        metadata_payload.update(archive_metadata)  # Add archive info if any
    if metadata_payload:
        result["metadata"] = metadata_payload

    return result


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("", response_model=TaskList)
async def list_tasks(
    project_id: str | None = Query(None, description="Filter by project ID"),
    status: TaskStatus | None = Query(None, description="Filter by status"),
):
    """List all tasks, optionally filtered by project or status."""
    projects = load_projects()

    # Filter projects
    if project_id:
        if project_id not in projects:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        project_ids = [project_id]
    else:
        project_ids = list(projects.keys())

    # Collect tasks from all projects
    all_tasks = []
    for pid in project_ids:
        project_path = Path(projects[pid]["path"])
        spec_dirs = get_spec_dirs(project_path)
        for spec_dir in spec_dirs:
            task = spec_to_task(pid, spec_dir)
            if status is None or task.status == status:
                all_tasks.append(task)

    # Sort by created_at descending
    all_tasks.sort(key=lambda t: t.created_at, reverse=True)

    return TaskList(tasks=all_tasks, total=len(all_tasks))


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get a specific task by ID.

    Returns full task details including execution progress and metadata
    (archivedAt, archivedInVersion).
    """
    # Parse task ID (format: project_id:spec_id)
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format. Expected 'project_id:spec_id'",
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

    task = spec_to_task(project_id, spec_dir)
    return task_to_dict(task)


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(task: TaskCreate):
    """Create a new task (spec) in a project."""
    projects = load_projects()

    if task.project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[task.project_id]["path"])

    # Ensure .tfactory/specs exists
    specs_dir = project_path / ".tfactory" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    # Generate spec ID and create directory
    spec_id = get_next_spec_id(project_path, task.title)
    spec_dir = specs_dir / spec_id
    spec_dir.mkdir()

    # Create initial spec.md
    spec_content = f"""# {task.title}

{task.description}

## Acceptance Criteria

- [ ] Feature works as described
- [ ] Tests pass
- [ ] Code review approved

## Notes

Created via Magestic AI Web UI
"""
    (spec_dir / "spec.md").write_text(spec_content)

    # Create requirements.json with metadata
    requirements: dict = {
        "title": task.title,
        "description": task.description,
        "created_at": datetime.now().isoformat(),
    }

    # Add metadata if provided
    if task.metadata:
        metadata_dict = task.metadata.model_dump(exclude_none=True)
        if metadata_dict:
            requirements["metadata"] = metadata_dict

            # Sync task_metadata.json for phase_config.py to read model/thinking settings
            # Also include selectedSkills so agent_service.py can inject skill context
            model_fields = [
                "model",
                "thinkingLevel",
                "isAutoProfile",
                "phaseModels",
                "phaseThinking",
                "mode",
                "selectedSkills",
            ]
            task_metadata = {
                field: metadata_dict[field]
                for field in model_fields
                if field in metadata_dict
            }
            if task_metadata:
                (spec_dir / "task_metadata.json").write_text(
                    json.dumps(task_metadata, indent=2)
                )

    (spec_dir / "requirements.json").write_text(json.dumps(requirements, indent=2))

    return spec_to_task(task.project_id, spec_dir)


# --------------------------------------------------------------------------
# Clarification Endpoints
# --------------------------------------------------------------------------


def _resolve_task(task_id: str) -> tuple[str, str, Path, Path]:
    """Resolve task_id (projectId:specId) to project_id, spec_id, project_path, spec_dir.

    Raises HTTPException on invalid input or missing resources.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=400, detail="Invalid task_id format (expected projectId:specId)"
        )

    project_id, spec_id = task_id.split(":", 1)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = Path(projects[project_id]["path"])
    spec_dir = safe_spec_dir(project_path, spec_id)

    if not spec_dir.exists():
        raise HTTPException(status_code=404, detail="Task spec not found")

    return project_id, spec_id, project_path, spec_dir


class TaskStatusUpdate(BaseModel):
    """Model for updating only task status (for kanban)."""

    status: TaskStatus
    force: bool = False  # Skip validation (e.g., after successful merge)


@router.patch("/{task_id}/status", response_model=Task)
async def update_task_status(task_id: str, update: TaskStatusUpdate):
    """Update a task's status (for kanban drag-and-drop)."""
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

    # Sync from worktree first to get latest progress
    plan, plan_file = get_plan_with_worktree_sync(project_path, spec_id)

    # Validate "done" status - ensure all subtasks are completed
    # Skip validation when force=True (e.g., after successful merge)
    if update.status == "done" and not update.force:
        is_valid, error_msg = validate_done_status(plan)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

    plan["status"] = update.status
    plan_file.write_text(json.dumps(plan, indent=2))

    # Auto-close linked GitHub issue when task is marked done
    if update.status == "done":
        _try_close_github_issue(project_path, spec_dir)

    return spec_to_task(project_id, spec_dir)


@router.put("/{task_id}", response_model=Task)
@router.patch("/{task_id}", response_model=Task)
async def update_task(task_id: str, update: TaskUpdate):
    """Update a task's metadata (supports both PUT and PATCH)."""
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

    # Update spec.md if title/description changed
    if update.title or update.description:
        spec_file = spec_dir / "spec.md"
        current_content = spec_file.read_text() if spec_file.exists() else ""

        if update.title:
            # Replace first heading
            current_content = re.sub(
                r"^#\s+.+$",
                f"# {update.title}",
                current_content,
                count=1,
                flags=re.MULTILINE,
            )

        if update.description:
            # Replace description paragraph (second section after title)
            # Split by double newline: [title, description, rest...]
            sections = current_content.split("\n\n", 2)
            if len(sections) >= 2:
                sections[1] = update.description
                current_content = "\n\n".join(sections)

        spec_file.write_text(current_content)

    # Update status in test_plan.json
    if update.status:
        # Sync from worktree first to get latest progress
        plan, plan_file = get_plan_with_worktree_sync(project_path, spec_id)

        # Validate "done" status - ensure all subtasks are completed
        if update.status == "done":
            is_valid, error_msg = validate_done_status(plan)
            if not is_valid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg,
                )

        plan["status"] = update.status
        plan_file.write_text(json.dumps(plan, indent=2))

    # Update requirements.json with title, description, and metadata
    requirements_file = spec_dir / "requirements.json"
    if update.title or update.description or update.metadata:
        requirements = {}
        if requirements_file.exists():
            try:
                requirements = json.loads(requirements_file.read_text())
            except json.JSONDecodeError:
                pass

        if update.title:
            requirements["title"] = update.title
        if update.description:
            requirements["description"] = update.description

        if update.metadata:
            if "metadata" not in requirements:
                requirements["metadata"] = {}

            # Get all fields that were explicitly set in the request (including None/null)
            # model_dump(exclude_unset=True) returns only fields that were explicitly set
            metadata_dict = update.metadata.model_dump(exclude_unset=True)

            # Process each field: null values clear the field, non-null values update it
            for field, value in metadata_dict.items():
                if value is None:
                    # Explicitly clear this field
                    requirements["metadata"].pop(field, None)
                else:
                    # Update the field
                    requirements["metadata"][field] = value

            # Sync task_metadata.json for phase_config.py to read model/thinking settings
            task_metadata_file = spec_dir / "task_metadata.json"
            task_metadata = {}
            if task_metadata_file.exists():
                try:
                    task_metadata = json.loads(task_metadata_file.read_text())
                except json.JSONDecodeError:
                    pass

            # Update model-related fields that phase_config.py expects
            # Also include selectedSkills so agent_service.py can inject skill context
            model_fields = [
                "model",
                "thinkingLevel",
                "isAutoProfile",
                "phaseModels",
                "phaseThinking",
                "mode",
                "requireReviewBeforeCoding",
                "selectedSkills",
            ]
            for field in model_fields:
                if field in metadata_dict:
                    if metadata_dict[field] is None:
                        # Clear field from task_metadata
                        task_metadata.pop(field, None)
                    else:
                        task_metadata[field] = metadata_dict[field]

            if task_metadata:
                task_metadata_file.write_text(json.dumps(task_metadata, indent=2))
            elif task_metadata_file.exists():
                # If all model fields were cleared, remove the file
                task_metadata_file.unlink()

        requirements_file.write_text(json.dumps(requirements, indent=2))

    return spec_to_task(project_id, spec_dir)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str):
    """Delete a task (removes its spec directory).

    Accepts both id shapes:
      - ``project_id:spec_id`` — an AIFactory-style task; resolve the project
        directly.
      - bare ``spec_id`` — a SPEC-INGEST task (created via /api/specs/ingest) is
        keyed by its spec_id with no project prefix, and that's what the cockpit's
        Remove action sends. Previously this 400'd ("Invalid task ID format"), so
        a failed ingested task was unremovable and kept reappearing via the
        reconcile poll. Resolve it by finding the project whose workspace holds
        the spec.
    """
    projects = load_projects()

    if ":" in task_id:
        project_id, spec_id = task_id.split(":", 1)
        if project_id not in projects:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        candidates = [projects[project_id]]
    else:
        # Bare spec_id (spec-ingest): search every project's workspace for it.
        spec_id = task_id
        candidates = list(projects.values())

    import shutil

    for entry in candidates:
        spec_dir = safe_spec_dir(Path(entry["path"]), spec_id)
        if spec_dir.exists():
            shutil.rmtree(spec_dir)
            return

    # Spec-ingest tasks (the verify lane, created via /api/specs/ingest) do NOT
    # live under a registered project path — they live in the MCP workspace tree
    # at ``<workspace_root>/workspaces/<project_id>/specs/<spec_id>``. The loop
    # above only checks the project-local layout, so an ingested verify task 404s
    # on delete and keeps RESURRECTING via the reconcile poll (the cockpit's
    # Remove reported "done" but the card came straight back). Search the
    # workspace tree too before giving up.
    import glob as _glob
    import os as _os

    # Same resolution as task_control._workspace_root (env override > default),
    # replicated inline to avoid a cross-package import from the web-server.
    _ws_root = Path(
        _os.environ.get("TFACTORY_WORKSPACE_ROOT") or (Path.home() / ".tfactory")
    ).expanduser()
    # spec_id is request-controlled and feeds a glob whose matches are rmtree'd;
    # reject any traversal/separator component before it reaches the filesystem.
    pattern = str(_ws_root / "workspaces" / "*" / "specs" / _validate_component(spec_id))
    for sd in _glob.glob(pattern):
        spec_dir = Path(sd)
        if spec_dir.exists():
            shutil.rmtree(spec_dir)
            return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Task not found",
    )


class ApprovePlanRequest(BaseModel):
    """Request to approve a plan."""

    auto_restart: bool = Field(True, description="Auto-restart task after approval")


class RejectPlanRequest(BaseModel):
    """Request to reject a plan with feedback for the planner.

    Mirrors ApprovePlanRequest's shape but carries the operator's reason so the
    planner's next iteration sees it in the spec's review feedback log.
    """

    feedback: str | None = Field(
        None,
        description="Optional reason for rejection — gets recorded on the review state's feedback log.",
    )


def _try_close_github_issue(project_path: Path, spec_dir: Path) -> None:
    """Try to close a linked GitHub issue. Logs but doesn't raise on failure."""
    try:
        req_file = spec_dir / "requirements.json"
        if not req_file.exists():
            return
        reqs = json.loads(req_file.read_text())
        # Check metadata.githubIssueNumber (set by task creation from issue)
        issue_number = None
        if isinstance(reqs.get("metadata"), dict):
            issue_number = reqs["metadata"].get("githubIssueNumber")
        # Also check githubIssue.number (set by import endpoint)
        if not issue_number and isinstance(reqs.get("githubIssue"), dict):
            issue_number = reqs["githubIssue"].get("number")
        if not issue_number:
            return
        from .github import run_gh_command

        result = run_gh_command(
            ["issue", "close", str(issue_number)],
            cwd=str(project_path),
        )
        if result["success"]:
            import logging

            logging.getLogger(__name__).info(
                f"Auto-closed GitHub issue #{issue_number}"
            )
        else:
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to auto-close GitHub issue #{issue_number}: {result.get('error', 'unknown')}"
            )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Error auto-closing GitHub issue: {e}")
