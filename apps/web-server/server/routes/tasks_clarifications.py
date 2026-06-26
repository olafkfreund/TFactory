"""Task clarification endpoints — extracted from routes/tasks.py (#360 split).

The two clarification endpoints carved out of routes/tasks.py. Behaviour and
paths unchanged; main.py mounts this under the same /api/tasks prefix. Shared
helpers/models still live in routes/tasks.py and are imported here.

    POST /api/tasks/{task_id}/clarifications
    POST /api/tasks/{task_id}/clarifications/answers
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter

from ._specpath import safe_component
from .tasks import (
    ClarificationAnswersRequest,
    ClarificationQuestion,
    ClarificationResponse,
    Task,
    _resolve_task,
    spec_to_task,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/{task_id}/clarifications", response_model=ClarificationResponse)
async def generate_clarifications(task_id: str):
    """Generate clarification questions for a task using an LLM."""
    from ..services.clarification_service import generate_clarification_questions

    # Reassign so the sanitized value is what flows into _resolve_task's path
    # build -- a CodeQL py/path-injection barrier must be intraprocedural.
    task_id = safe_component(task_id)

    project_id, spec_id, project_path, spec_dir = _resolve_task(task_id)

    # Load task title and description from requirements.json
    req_file = spec_dir / "requirements.json"
    if not req_file.exists():
        return ClarificationResponse(skip=True, skipReason="No requirements found.")

    requirements = json.loads(req_file.read_text())
    title = requirements.get("title", "")
    description = requirements.get("description", "")

    result = await generate_clarification_questions(title, description, project_path)

    return ClarificationResponse(
        questions=[ClarificationQuestion(**q) for q in result.get("questions", [])],
        skip=result.get("skip", False),
        skipReason=result.get("skipReason", ""),
    )


@router.post("/{task_id}/clarifications/answers", response_model=Task)
async def submit_clarification_answers(
    task_id: str, request: ClarificationAnswersRequest
):
    """Submit answers to clarification questions and append them to the task."""
    # Reassign so the sanitized value is what flows into _resolve_task's path
    # build -- a CodeQL py/path-injection barrier must be intraprocedural.
    task_id = safe_component(task_id)

    project_id, spec_id, project_path, spec_dir = _resolve_task(task_id)

    if not request.answers:
        return spec_to_task(project_id, spec_dir)

    # Build clarification appendix
    lines = ["\n\n## Clarifications\n"]
    for answer in request.answers:
        if answer.answer.strip():
            lines.append(f"**Q: {answer.question}**")
            lines.append(f"A: {answer.answer.strip()}\n")
    appendix = "\n".join(lines)

    # Update requirements.json description
    req_file = spec_dir / "requirements.json"
    if req_file.exists():
        requirements = json.loads(req_file.read_text())
        requirements["description"] = requirements.get("description", "") + appendix
        req_file.write_text(json.dumps(requirements, indent=2))

    # Append to spec.md
    spec_file = spec_dir / "spec.md"
    if spec_file.exists():
        content = spec_file.read_text()
        # Insert before ## Notes section if it exists, otherwise append
        if "\n## Notes\n" in content:
            content = content.replace("\n## Notes\n", f"{appendix}\n## Notes\n")
        else:
            content += appendix
        spec_file.write_text(content)

    return spec_to_task(project_id, spec_dir)
