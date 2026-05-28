"""Tests for the real run_evaluator + auto-fire scaffold —
Task 7 (#8) commit 5.

Mocks the two SDK seams (_resolve_evaluator_client, _invoke_session)
and the runner_fn seam (_resolve_runner_fn) so the loop runs
deterministically without docker. The four numeric primitives
(coverage_delta, stability, mutation, lint_promotion) are exercised
for real where they're cheap; the docker-shaped ones use the mocked
runner_fn that returns canned exit codes.

Covered:
  - Happy path: 1 completed subtask → bundle built → SDK writes
    verdict → status=evaluated, verdicts_count=1
  - Multi-subtask happy path
  - No completed subtasks → evaluated_empty (early exit, no SDK call)
  - Missing test_plan.json → evaluator_failed (phase=evaluator_no_plan)
  - Malformed test_plan.json → evaluator_failed
  - Agent didn't write verdicts.json → evaluator_failed
  - Verdicts.json malformed (invalid JSON, wrong shape, bad verdict
    value) → evaluator_failed with specific error message
  - Session error → evaluator_failed with error captured
  - Signal bundles are passed into the prompt (via the captured prompt)
  - schedule_evaluator env-gate (carried over from commit 1 — still
    works after rewrite)
  - Forward chain from gen_functional still fires (carried over)
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.evaluator import (
    _BG_EVALUATOR_TASKS,
    EvaluatorSignals,
    run_evaluator,
    schedule_evaluator,
)


# ── autouse: keep the chain envs deterministic ─────────────────────────


@pytest.fixture(autouse=True)
def _disable_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")


# ── Workspace fixture ──────────────────────────────────────────────────


def _make_test_plan(num_completed: int = 1) -> dict:
    """Build a test_plan.json with N completed functional subtasks."""
    subtasks = []
    for i in range(num_completed):
        subtasks.append({
            "id": f"st{i}",
            "description": f"Subtask {i}",
            "status": "completed",
            "lane": "functional",
            "target": f"app/m{i}.py::f{i}",
            "rationale": f"AC#{i+1}",
            "files_to_create": [f"tests/test_{i}.py"],
            "verification": {
                "type": "command",
                "command": f"pytest tests/test_{i}.py",
            },
        })
    return {
        "feature": "x", "workflow_type": "feature", "services_involved": [],
        "phases": [{
            "phase": 1, "name": "main", "type": "implementation",
            "subtasks": subtasks, "parallel_safe": False,
        }],
        "final_acceptance": [], "status": "in_progress", "planStatus": "pending",
    }


def _write_test_file(spec_dir: Path, relpath: str) -> Path:
    """Write a clean, lint-passing pytest file."""
    f = spec_dir / relpath
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(textwrap.dedent('''
        """Test file."""
        def test_x():
            assert 1 == 1
    ''').lstrip())
    return f


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-feat"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(json.dumps({
        "task_id": "001-feat",
        "project_id": "demo",
        "spec_id": "001-feat",
        "status": "generated",
        "phase": "gen_functional_complete",
        "tests_generated": 1,
    }))
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


# ── Mocks ──────────────────────────────────────────────────────────────


def _install_runner_mock(
    monkeypatch: pytest.MonkeyPatch, returncode: int = 0,
) -> None:
    """Replace _resolve_runner_fn with a fixture that doesn't touch docker."""
    class _FakeResult:
        def __init__(self, rc: int):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def _runner(test_file, project_dir, seed):
        return _FakeResult(returncode)

    def _resolve(spec_dir, project_dir):
        return _runner

    monkeypatch.setattr("agents.evaluator._resolve_runner_fn", _resolve)


def _install_sdk_mocks(
    monkeypatch: pytest.MonkeyPatch,
    verdicts_writer,  # Callable[[Path, list[EvaluatorSignals], str], None]
    captured_prompt: list[str] | None = None,
) -> None:
    """Mock the SDK seams; on _invoke_session, call verdicts_writer."""
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None

    async def _resolve(*_a, **_kw):
        return _CM()

    async def _invoke(_client, prompt, spec_dir_arg, _verbose):
        if captured_prompt is not None:
            captured_prompt.append(prompt)
        verdicts_writer(spec_dir_arg, prompt)
        return "complete", "ok", {}

    monkeypatch.setattr("agents.evaluator._resolve_evaluator_client", _resolve)
    monkeypatch.setattr("agents.evaluator._invoke_session", _invoke)


def _good_verdicts(test_ids: list[str], dest: Path) -> None:
    """Write a well-formed verdicts.json."""
    doc = {
        "evaluator_version": "task7-commit5",
        "mode": "initial",
        "verdicts": [
            {
                "test_id": tid,
                "test_file": f"tests/test_{tid}.py",
                "verdict": "accept",
                "reasons": ["all signals green"],
                "signals_summary": {
                    "coverage_delta_pct": 0.0,
                    "coverage_new_lines": 0,
                    "stability": "stable",
                    "mutation": "killed",
                    "lint_promotion": "no findings",
                },
                "semantic_relevance": "high",
                "semantic_notes": "test matches rationale",
            }
            for tid in test_ids
        ],
        "generated_at": "2026-05-28T00:00:00+00:00",
    }
    dest.write_text(json.dumps(doc, indent=2))


# ── Happy paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_single_subtask(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch, returncode=0)

    def _write(spec_dir_arg, _prompt):
        _good_verdicts(["st0"], spec_dir_arg / "findings" / "verdicts.json")

    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir, mode="initial")
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluated"
    assert status["phase"] == "evaluator_complete"
    assert status["verdicts_count"] == 1
    assert status["tests_evaluated"] == 1


@pytest.mark.asyncio
async def test_happy_multi_subtask(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(3)))
    for i in range(3):
        _write_test_file(spec_dir, f"tests/test_{i}.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        _good_verdicts(
            ["st0", "st1", "st2"],
            spec_dir_arg / "findings" / "verdicts.json",
        )

    _install_sdk_mocks(monkeypatch, _write)
    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["verdicts_count"] == 3
    assert status["tests_evaluated"] == 3


@pytest.mark.asyncio
async def test_no_completed_subtasks_is_evaluated_empty(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No SDK call should happen — early exit at evaluated_empty."""
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(0)))
    _install_runner_mock(monkeypatch)

    sdk_called = {"n": 0}
    def _write(spec_dir_arg, _prompt):
        sdk_called["n"] += 1
    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is True
    assert sdk_called["n"] == 0  # SDK NOT called
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluated_empty"
    assert status["verdicts_count"] == 0


# ── Plan loading failures ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_plan_is_evaluator_failed(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No test_plan.json written
    _install_runner_mock(monkeypatch)
    _install_sdk_mocks(monkeypatch, lambda *a: None)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert status["phase"] == "evaluator_no_plan"


@pytest.mark.asyncio
async def test_malformed_plan_is_evaluator_failed(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text("not json at all")
    _install_runner_mock(monkeypatch)
    _install_sdk_mocks(monkeypatch, lambda *a: None)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert status["phase"] == "evaluator_plan_unparseable"


# ── Verdicts.json validation failures ──────────────────────────────────


@pytest.mark.asyncio
async def test_agent_didnt_write_verdicts_is_evaluator_failed(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)
    _install_sdk_mocks(monkeypatch, lambda *a: None)  # writes NOTHING

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert status["phase"] == "evaluator_invalid_verdicts"
    assert "not written" in status["evaluator_error"]


@pytest.mark.asyncio
async def test_verdicts_invalid_json(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text("not json {")
    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert "not valid JSON" in status["evaluator_error"]


@pytest.mark.asyncio
async def test_verdicts_missing_array(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text(json.dumps({
            "evaluator_version": "x", "verdicts": "not an array",
        }))
    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert "missing 'verdicts' array" in status["evaluator_error"]


@pytest.mark.asyncio
async def test_verdicts_invalid_verdict_value(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text(json.dumps({
            "evaluator_version": "x",
            "verdicts": [{"test_id": "st0", "verdict": "maybe-yes"}],
        }))
    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert "invalid 'verdict'" in status["evaluator_error"]
    assert "maybe-yes" in status["evaluator_error"]


@pytest.mark.asyncio
async def test_verdicts_missing_test_id(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text(json.dumps({
            "verdicts": [{"verdict": "accept"}],  # no test_id
        }))
    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert "missing 'test_id'" in status["evaluator_error"]


# ── Session error ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_error_is_evaluator_failed(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()
    async def _invoke(*_a, **_kw):
        raise RuntimeError("session blew up")
    monkeypatch.setattr("agents.evaluator._resolve_evaluator_client", _resolve)
    monkeypatch.setattr("agents.evaluator._invoke_session", _invoke)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert status["phase"] == "evaluator_session_error"
    assert "session blew up" in status["evaluator_error"]


# ── Signal bundle assembly ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_includes_signal_context(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt the agent sees should include the per-test
    EVALUATOR CONTEXT block with the subtask's id + target."""
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    captured: list[str] = []
    def _write(spec_dir_arg, _prompt):
        _good_verdicts(["st0"], spec_dir_arg / "findings" / "verdicts.json")
    _install_sdk_mocks(monkeypatch, _write, captured_prompt=captured)

    await run_evaluator(spec_dir, project_dir)

    assert len(captured) == 1
    prompt = captured[0]
    assert "EVALUATOR CONTEXT" in prompt
    assert "st0" in prompt
    assert "app/m0.py::f0" in prompt
    assert "AC#1" in prompt
    # Mutation primitive ran (with mocked runner) — should be reflected
    # in the per-test block. We expect "mutation: killed" since
    # returncode=0 means SURVIVED, but the test source asserts 1==1
    # which the mutator flips to 1!=1, and the FAKE runner returns 0
    # regardless — so the mutated test "passes" → SURVIVED.
    # That's actually a useful sanity check: verifies the mutator ran.
    assert "mutation:" in prompt


# ── EvaluatorSignals dataclass surface ─────────────────────────────────


def test_evaluator_signals_dataclass() -> None:
    """Sanity: the bundle dataclass has the documented fields."""
    sig = EvaluatorSignals(
        test_id="x", test_file=Path("/x.py"),
        target="a::b", rationale="ac",
    )
    assert sig.test_id == "x"
    assert sig.coverage_delta is None
    assert sig.stability is None
    assert sig.mutation is None
    assert sig.lint_promotion is None


# ── schedule_evaluator: env gating + GC anchor (carried over) ──────────


def test_schedule_disabled_returns_none(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")
    async def _run():
        return schedule_evaluator(spec_dir, project_dir)
    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")
    # Make the real-path early-exit at evaluated_empty (no plan)
    # by NOT writing test_plan.json — the task will land at
    # evaluator_failed/evaluator_no_plan but the schedule semantics
    # are what we're verifying.
    task = schedule_evaluator(spec_dir, project_dir)
    assert task is not None
    assert task in _BG_EVALUATOR_TASKS
    await task
    assert task not in _BG_EVALUATOR_TASKS


# ── Forward chain from gen_functional (carried over) ───────────────────


@pytest.mark.asyncio
async def test_gen_functional_success_path_schedules_evaluator(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")
    captured: dict = {}

    def _capture(sd, pd, mode="initial"):
        captured["spec_dir"] = sd
        captured["project_dir"] = pd
        captured["mode"] = mode
        return None

    import agents.evaluator as eval_mod
    monkeypatch.setattr(eval_mod, "schedule_evaluator", _capture)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)
    assert captured["spec_dir"] == spec_dir
    assert captured["mode"] == "initial"


def test_advance_to_evaluator_swallows_import_errors(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _selective_raiser(name, *args, **kwargs):
        if name == "agents.evaluator":
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_selective_raiser):
        gen_functional._advance_to_evaluator(spec_dir, project_dir)
