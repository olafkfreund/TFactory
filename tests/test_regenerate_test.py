"""Hermetic tests for the evaluator's single-test generation entry point."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from agents import gen_functional

_ORIGINAL = "tests/test_total.py"
_REPLACEMENT = "tests/test_total_regen1.py"
_SOURCE = "def test_total():\n    assert 11.76 == 11.76\n"
_BRIEF = "Check the original acceptance criterion and its edge cases."


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
    state = {
        spec_dir / "test_plan.json": json.dumps({"subtasks": [subtask]}).encode(),
        spec_dir / "status.json": b'{"status":"evaluating","tests_generated":1}',
    }
    for path, content in state.items():
        path.write_bytes(content)

    forbidden = Mock(side_effect=AssertionError("regeneration must not replan"))
    monkeypatch.setattr(gen_functional, "_reject_subtask_for_replan", forbidden)
    monkeypatch.setattr(gen_functional, "_advance_to_planner_replan", forbidden)
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "1")
    monkeypatch.setattr(gen_functional, "_resolve_client", AsyncMock())
    monkeypatch.setattr(
        gen_functional, "_invoke_session", AsyncMock(side_effect=AssertionError)
    )
    yield spec_dir, project_dir, subtask

    forbidden.assert_not_called()
    assert original.read_bytes() == original_bytes
    for path, content in state.items():
        assert path.read_bytes() == content
    assert not (spec_dir / "context/replan_request.json").exists()


def _stub_session(monkeypatch, *, source=_SOURCE, status="complete", error=None):
    async def invoke(_client, prompt, spec_dir, _verbose):
        if source is not None:
            instruction = next(
                line
                for line in prompt.splitlines()
                if line.startswith("- write the file at:")
            )
            target = Path(instruction.split("`")[1])
            assert target == spec_dir / _REPLACEMENT
            target.write_text(source)
        if error is not None:
            raise error
        return status, "", {}

    session = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(gen_functional, "_invoke_session", session)
    return session


async def test_regenerate_writes_a_separate_test(workspace, monkeypatch):
    spec_dir, project_dir, subtask = workspace
    before = deepcopy(subtask)
    session = _stub_session(monkeypatch)

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
    session.assert_awaited_once()
    assert session.call_args.args[1].endswith("\n\n" + _BRIEF)


@pytest.mark.parametrize(
    "session_options",
    [
        pytest.param({"status": "error"}, id="session-error-after-write"),
        pytest.param({"source": None}, id="no-file"),
        pytest.param({"source": _SOURCE.replace("11.76", "11.75")}, id="guardrail"),
        pytest.param({"error": RuntimeError("SDK failed")}, id="exception-after-write"),
    ],
)
async def test_regenerate_discards_failed_attempt(
    workspace, monkeypatch, session_options
):
    spec_dir, project_dir, subtask = workspace
    session = _stub_session(monkeypatch, **session_options)

    assert (
        await gen_functional.regenerate_test(
            spec_dir, subtask, _BRIEF, project_dir=project_dir
        )
        is None
    )
    session.assert_awaited_once()
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
