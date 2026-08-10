"""In-process spec + plan authoring (#779).

The old standard spec-creation path shelled out to `runners/spec_runner.py`,
which was never packaged into the image → create-and-run / start 500'd with
exit 2 before any planning. `_author_spec_and_plan` now writes spec.md +
test_plan.json deterministically instead. These tests cover the self-contained
"simple" shape (no planner_lib / no backend on path required).
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from server.services.agent_service import AgentService  # noqa: E402


def test_simple_authors_spec_plan_and_approval(tmp_path: Path) -> None:
    service = AgentService()
    spec_dir = tmp_path / "specs" / "042"

    service._author_spec_and_plan(
        spec_dir,
        title="Add a health endpoint",
        description="Return 200 from /health",
        complexity="simple",
        approve=True,
    )

    spec = (spec_dir / "spec.md").read_text()
    assert "Add a health endpoint" in spec and "Return 200 from /health" in spec

    plan = json.loads((spec_dir / "test_plan.json").read_text())
    subtasks = plan["phases"][0]["subtasks"]
    assert len(subtasks) == 1 and subtasks[0]["status"] == "pending"

    review = json.loads((spec_dir / "review_state.json").read_text())
    assert review["approved"] is True and review["approved_by"] == "auto-inprocess"


def test_no_approval_file_when_review_required(tmp_path: Path) -> None:
    service = AgentService()
    spec_dir = tmp_path / "specs" / "043"

    service._author_spec_and_plan(
        spec_dir,
        title="Risky change",
        description="",
        complexity="simple",
        approve=False,
    )

    # Plan is written, but the human review gate is left unsatisfied.
    assert (spec_dir / "test_plan.json").exists()
    assert not (spec_dir / "review_state.json").exists()


def test_standard_falls_back_to_single_subtask_without_project_context(
    tmp_path: Path,
) -> None:
    # planner_lib yields no phases for a fresh spec with no project_index.json;
    # run.py needs at least one actionable subtask, so the plan must not be empty.
    import types

    service = AgentService()
    service.settings = types.SimpleNamespace(
        BACKEND_PATH=str(Path(__file__).resolve().parents[2] / "backend")
    )
    spec_dir = tmp_path / "specs" / "060"

    service._author_spec_and_plan(
        spec_dir,
        title="Add retry to fetch",
        description="wrap fetch in a 3-attempt retry",
        complexity="standard",
        approve=True,
    )

    plan = json.loads((spec_dir / "test_plan.json").read_text())
    assert plan["phases"], "empty plan would make run.py no-op"
    assert plan["phases"][0]["subtasks"]


def test_existing_spec_md_is_not_clobbered(tmp_path: Path) -> None:
    service = AgentService()
    spec_dir = tmp_path / "specs" / "044"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Hand-authored spec\n\nKeep me.\n")

    service._author_spec_and_plan(
        spec_dir,
        title="Ignored title",
        description="Ignored description",
        complexity="simple",
        approve=True,
    )

    assert "Keep me." in (spec_dir / "spec.md").read_text()
