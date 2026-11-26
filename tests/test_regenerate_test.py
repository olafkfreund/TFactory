"""Hermetic tests for the evaluator's single-test generation entry point."""

import asyncio
import json
from copy import deepcopy
from itertools import repeat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from agents import gen_functional
from prompts_pkg import prompts
from test_plan import ImplementationPlan, Subtask

_ORIGINAL = "tests/test_total.py"
_REPLACEMENT = "tests/test_total_regen1.py"
_SOURCE = "def test_total():\n    assert 11.76 == 11.76\n"
_BRIEF = "Check the original acceptance criterion and its edge cases."
_Workspace = tuple[Path, Path, dict[str, Any]]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    spec_dir = tmp_path / "spec"
    (spec_dir / "tests").mkdir(parents=True)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    subtask = {
        "id": "s0",
        "description": "the order total is 11.76",
        "status": "completed",
        "lane": "unit",
        "language": "python",
        "framework": "pytest",
        "files_to_create": [_ORIGINAL],
        "replan_count": 1,
        "patterns_from": ["reference.py"],
    }
    original = spec_dir / _ORIGINAL
    original_bytes = b"def test_total():\n    assert 11.75 == 11.75\n"
    original.write_bytes(original_bytes)
    plan_path = spec_dir / "test_plan.json"
    plan_bytes = json.dumps({"subtasks": [subtask]}).encode()
    plan_path.write_bytes(plan_bytes)
    status_path = spec_dir / "status.json"
    original_status = {"status": "evaluating", "tests_generated": 1}
    status_path.write_text(json.dumps(original_status))

    forbidden = Mock(side_effect=AssertionError("regeneration must not replan"))
    monkeypatch.setattr(gen_functional, "_reject_subtask_for_replan", forbidden)
    monkeypatch.setattr(gen_functional, "_advance_to_planner_replan", forbidden)
    monkeypatch.setattr(gen_functional, "_waive_repeated_literal_rejection", forbidden)
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "1")
    monkeypatch.delenv("TFACTORY_GEN_TWO_STEP", raising=False)
    monkeypatch.setattr(gen_functional, "_resolve_client", AsyncMock())
    monkeypatch.setattr(
        gen_functional, "_invoke_session", AsyncMock(side_effect=AssertionError)
    )
    yield spec_dir, project_dir, subtask

    forbidden.assert_not_called()
    assert original.read_bytes() == original_bytes
    assert plan_path.read_bytes() == plan_bytes
    status = json.loads(status_path.read_text())
    for key in (
        "import_rewrites",
        "import_retries",
        "unresolvable_imports",
        "gen_functional_warnings",
        "updated_at",
    ):
        status.pop(key, None)
    assert status == original_status
    assert not (spec_dir / "context/replan_request.json").exists()


def _stub_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str | tuple[str, ...] | None = _SOURCE,
    status: str = "complete",
    error: BaseException | None = None,
) -> AsyncMock:
    sources = iter(source) if isinstance(source, tuple) else repeat(source)

    async def invoke(
        _client: object, prompt: str, spec_dir: Path, _verbose: bool
    ) -> tuple[str, str, dict[str, Any]]:
        current = next(sources)
        if current is not None:
            instruction = next(
                line
                for line in prompt.splitlines()
                if line.startswith("- write the file at:")
            )
            target = Path(instruction.split("`")[1])
            assert target == spec_dir / _REPLACEMENT
            target.write_text(current)
        if error is not None:
            raise error
        return status, "", {}

    session = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(gen_functional, "_invoke_session", session)
    return session


@pytest.mark.parametrize("steps", [("assert", "wire"), ("single",)])
async def test_regenerate_writes_a_separate_test(
    workspace: _Workspace, monkeypatch: pytest.MonkeyPatch, steps: tuple[str, ...]
) -> None:
    spec_dir, project_dir, subtask = workspace
    if steps == ("single",):
        monkeypatch.setenv("TFACTORY_GEN_TWO_STEP", "0")
    before = deepcopy(subtask)
    session = _stub_session(monkeypatch)
    build_prompt = Mock(wraps=prompts.get_tfactory_gen_functional_prompt)
    monkeypatch.setattr(prompts, "get_tfactory_gen_functional_prompt", build_prompt)

    result = await gen_functional.regenerate_test(
        spec_dir, subtask, _BRIEF, project_dir=project_dir
    )

    assert isinstance(result, gen_functional.GeneratedTest)
    assert result.test_id == "s0-regen1"
    assert result.test_file == _REPLACEMENT
    assert (spec_dir / result.test_file).read_text() == _SOURCE
    assert result.subtask == {
        **before,
        "id": "s0-regen1",
        "files_to_create": [_REPLACEMENT],
        "status": "completed",
    }
    assert result.subtask["patterns_from"] is not subtask["patterns_from"]
    assert subtask == before
    assert tuple(call.kwargs["step"] for call in build_prompt.call_args_list) == steps
    assert session.await_count == len(steps)
    prompts_used = [call.args[1] for call in session.await_args_list]
    assert all(prompt.endswith("\n\n" + _BRIEF) for prompt in prompts_used)


async def test_pending_and_regeneration_share_generation(
    workspace: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_dir, project_dir, subtask = workspace
    shared = AsyncMock(wraps=gen_functional._generate_and_guard)
    monkeypatch.setattr(gen_functional, "_generate_and_guard", shared)
    _stub_session(monkeypatch)

    result = await gen_functional.regenerate_test(
        spec_dir, subtask, _BRIEF, project_dir=project_dir
    )
    assert result is not None
    pending = Subtask.from_dict(
        {**result.subtask, "id": "pending", "status": "pending"}
    )
    outcome = await gen_functional._generate_one_subtask(
        spec_dir,
        project_dir,
        ImplementationPlan(feature="order totals"),
        spec_dir / "test_plan.json",
        pending,
        0,
        False,
    )
    assert outcome == "generated"
    calls = shared.await_args_list
    assert [call.args[2].id for call in calls] == ["s0-regen1", "pending"]
    assert [call.kwargs.get("brief") for call in calls] == [_BRIEF, None]


async def test_regenerate_threads_brief_through_import_retry(
    workspace: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_dir, project_dir, subtask = workspace
    monkeypatch.setenv("TFACTORY_GEN_IMPORT_RETRIES", "1")
    session = _stub_session(
        monkeypatch, source=(_SOURCE, 'import "./missing";\n' + _SOURCE, _SOURCE)
    )

    result = await gen_functional.regenerate_test(
        spec_dir, subtask, _BRIEF, project_dir=project_dir
    )
    assert result is not None
    assert session.await_count == 3
    assert all(call.args[1].endswith(_BRIEF) for call in session.await_args_list)
    assert "## IMPORT FIX REQUIRED" in session.await_args_list[-1].args[1]


async def test_regenerate_rejects_changed_assertion_values(
    workspace: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_dir, project_dir, subtask = workspace
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "0")
    session = _stub_session(
        monkeypatch, source=(_SOURCE, _SOURCE.replace("11.76", "11.75"))
    )

    assert (
        await gen_functional.regenerate_test(
            spec_dir, subtask, _BRIEF, project_dir=project_dir
        )
        is None
    )
    assert session.await_count == 2
    assert not (spec_dir / _REPLACEMENT).exists()


@pytest.mark.parametrize(
    ("session_options", "sessions"),
    [
        pytest.param({"status": "error"}, 1, id="session-error-after-write"),
        pytest.param({"source": None}, 1, id="no-file"),
        pytest.param({"source": _SOURCE.replace("11.76", "11.75")}, 2, id="guardrail"),
        pytest.param(
            {"error": RuntimeError("SDK failed")}, 1, id="exception-after-write"
        ),
    ],
)
async def test_regenerate_discards_failed_attempt(
    workspace: _Workspace,
    monkeypatch: pytest.MonkeyPatch,
    session_options: dict[str, Any],
    sessions: int,
) -> None:
    spec_dir, project_dir, subtask = workspace
    session = _stub_session(monkeypatch, **session_options)

    assert (
        await gen_functional.regenerate_test(
            spec_dir, subtask, _BRIEF, project_dir=project_dir
        )
        is None
    )
    assert session.await_count == sessions
    assert not (spec_dir / _REPLACEMENT).exists()


async def test_regenerate_propagates_cancellation(workspace, monkeypatch):
    spec_dir, project_dir, subtask = workspace
    _stub_session(monkeypatch, error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await gen_functional.regenerate_test(
            spec_dir, subtask, _BRIEF, project_dir=project_dir
        )
    assert not (spec_dir / _REPLACEMENT).exists()


@pytest.mark.parametrize("existing", [False, True])
async def test_regenerate_skips_without_a_session(workspace, existing):
    spec_dir, project_dir, subtask = workspace
    replacement = spec_dir / _REPLACEMENT
    if existing:
        replacement.write_text("earlier replacement")
    else:
        subtask.pop("files_to_create")

    assert (
        await gen_functional.regenerate_test(
            spec_dir, subtask, _BRIEF, project_dir=project_dir
        )
        is None
    )
    gen_functional._resolve_client.assert_not_called()
    gen_functional._invoke_session.assert_not_called()
    if existing:
        assert replacement.read_text() == "earlier replacement"
    else:
        assert not replacement.exists()


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("test_x.py", "test_x_regen1.py"),
        ("x.test.ts", "x_regen1.test.ts"),
        ("x.spec.ts", "x_regen1.spec.ts"),
        ("foo_test.go", "foo_regen1_test.go"),
    ],
)
def test_regenerated_test_path_keeps_framework_suffix(original, replacement):
    parent = Path("nested/tests")
    assert (
        gen_functional._regenerated_test_path(parent / original) == parent / replacement
    )
