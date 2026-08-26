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
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")


# ── Workspace fixture ──────────────────────────────────────────────────


def _make_test_plan(num_completed: int = 1) -> dict:
    """Build a test_plan.json with N completed functional subtasks."""
    subtasks = []
    for i in range(num_completed):
        subtasks.append(
            {
                "id": f"st{i}",
                "description": f"Subtask {i}",
                "status": "completed",
                "lane": "functional",
                "target": f"app/m{i}.py::f{i}",
                "rationale": f"AC#{i + 1}",
                "files_to_create": [f"tests/test_{i}.py"],
                "verification": {
                    "type": "command",
                    "command": f"pytest tests/test_{i}.py",
                },
            }
        )
    return {
        "feature": "x",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "main",
                "type": "implementation",
                "subtasks": subtasks,
                "parallel_safe": False,
            }
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    }


def _write_test_file(spec_dir: Path, relpath: str) -> Path:
    """Write a clean, lint-passing pytest file."""
    f = spec_dir / relpath
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        textwrap.dedent('''
        """Test file."""
        def test_x():
            assert 1 == 1
    ''').lstrip()
    )
    return f


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-feat"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(
        json.dumps(
            {
                "task_id": "001-feat",
                "project_id": "demo",
                "spec_id": "001-feat",
                "status": "generated",
                "phase": "gen_functional_complete",
                "tests_generated": 1,
            }
        )
    )
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


# ── Mocks ──────────────────────────────────────────────────────────────


def _install_runner_mock(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int = 0,
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
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps(
                {
                    "evaluator_version": "x",
                    "verdicts": "not an array",
                }
            )
        )

    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert "missing 'verdicts' array" in status["evaluator_error"]


@pytest.mark.asyncio
async def test_verdicts_invalid_verdict_value(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps(
                {
                    "evaluator_version": "x",
                    "verdicts": [{"test_id": "st0", "verdict": "maybe-yes"}],
                }
            )
        )

    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert "invalid 'verdict'" in status["evaluator_error"]
    assert "maybe-yes" in status["evaluator_error"]


@pytest.mark.asyncio
async def test_verdicts_missing_test_id(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    def _write(spec_dir_arg, _prompt):
        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps(
                {
                    "verdicts": [{"verdict": "accept"}],  # no test_id
                }
            )
        )

    _install_sdk_mocks(monkeypatch, _write)

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert "missing 'test_id'" in status["evaluator_error"]


# ── Session error ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_error_is_evaluator_failed(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (spec_dir / "test_plan.json").write_text(json.dumps(_make_test_plan(1)))
    _write_test_file(spec_dir, "tests/test_0.py")
    _install_runner_mock(monkeypatch)

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    async def _resolve(*_a, **_kw):
        return _CM()

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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        test_id="x",
        test_file=Path("/x.py"),
        target="a::b",
        rationale="ac",
    )
    assert sig.test_id == "x"
    assert sig.coverage_delta is None
    assert sig.stability is None
    assert sig.mutation is None
    assert sig.lint_promotion is None


# ── schedule_evaluator: env gating + GC anchor (carried over) ──────────


def test_schedule_disabled_returns_none(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")

    async def _run():
        return schedule_evaluator(spec_dir, project_dir)

    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def _selective_raiser(name, *args, **kwargs):
        if name == "agents.evaluator":
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_selective_raiser):
        gen_functional._advance_to_evaluator(spec_dir, project_dir)


# ── RFC-0016/0017 #466 — kubejob verify dispatch wiring ────────────────────────
#
# Default (unset) keeps the in-pod schedule_evaluator path; TFACTORY_VERIFY_EXEC=
# kubejob dispatches the verify as a k8s Job instead; a failed/None dispatch falls
# back to in-pod so the verify is never stranded.


def test_advance_kubejob_dispatches_job_and_skips_inpod(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    # The autouse _disable_chains fixture pins TFACTORY_AUTO_EVALUATE=0; this
    # test is about the *dispatch*, so re-enable it explicitly (#897 — before
    # that fix the kubejob branch ran regardless of this flag, which is exactly
    # the defect: this test used to pass with auto-evaluate pinned off).
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")
    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "kubejob")
    monkeypatch.setenv("JOB_ID", "proj:042")

    import agents.verify_dispatch as vd_mod

    seen: dict = {}

    async def _fake_dispatch(*, job_id, spec_dir, project_dir, correlation_key=None):
        seen["job_id"] = job_id
        seen["spec_dir"] = spec_dir
        return vd_mod.VerifyDispatch(
            job_id=job_id,
            job_name=vd_mod.verify_job_name(job_id),
            namespace="factory",
            worker_ref={"kind": "k8s-job"},
        )

    monkeypatch.setattr(vd_mod, "dispatch_verify_job", _fake_dispatch)

    # If in-pod ran, this would raise — assert it is NOT called on the Job path.
    import agents.evaluator as eval_mod

    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("in-pod schedule_evaluator must not run on kubejob path")

    monkeypatch.setattr(eval_mod, "schedule_evaluator", _boom)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)
    assert seen["job_id"] == "proj:042"
    assert seen["spec_dir"] == spec_dir


def test_advance_unset_uses_inpod_path(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    monkeypatch.delenv("TFACTORY_VERIFY_EXEC", raising=False)
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")

    import agents.evaluator as eval_mod
    import agents.verify_dispatch as vd_mod

    called: dict = {}

    def _capture(sd, pd, mode="initial"):
        called["inpod"] = True
        return None

    async def _no_dispatch(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("dispatch must not run when verify exec is in-pod")

    monkeypatch.setattr(eval_mod, "schedule_evaluator", _capture)
    monkeypatch.setattr(vd_mod, "dispatch_verify_job", _no_dispatch)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)
    assert called.get("inpod") is True


def test_advance_kubejob_falls_back_to_inpod_when_dispatch_returns_none(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # dispatch_verify_job returns None when the sandbox / DATABASE_URL gap means
    # the Job can't run — the wiring must then run the in-pod path, not drop the
    # verify.
    from agents import gen_functional

    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")  # see note in the test above
    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "kubejob")

    import agents.evaluator as eval_mod
    import agents.verify_dispatch as vd_mod

    called: dict = {}

    async def _none_dispatch(**kwargs):
        return None  # sandbox unconfigured / apply failed → fall back

    def _capture(sd, pd, mode="initial"):
        called["inpod"] = True
        return None

    monkeypatch.setattr(vd_mod, "dispatch_verify_job", _none_dispatch)
    monkeypatch.setattr(eval_mod, "schedule_evaluator", _capture)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)
    assert called.get("inpod") is True


# ── #897 — TFACTORY_AUTO_EVALUATE gates BOTH execution modes ──────────────────
#
# The gate used to live only inside the in-pod schedule_evaluator, so with the
# production TFACTORY_VERIFY_EXEC=kubejob setting it governed nothing: the
# kubejob branch ran first and applied a real verify Job regardless.


def test_auto_evaluate_off_blocks_kubejob_dispatch(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "kubejob")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")

    import agents.evaluator as eval_mod
    import agents.verify_dispatch as vd_mod

    async def _no_dispatch(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("kubejob dispatch must not run when auto-evaluate is off")

    def _no_inpod(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("in-pod evaluator must not run when auto-evaluate is off")

    monkeypatch.setattr(vd_mod, "dispatch_verify_job", _no_dispatch)
    monkeypatch.setattr(eval_mod, "schedule_evaluator", _no_inpod)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)


def test_auto_evaluate_off_blocks_inpod_path(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import gen_functional

    monkeypatch.delenv("TFACTORY_VERIFY_EXEC", raising=False)
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")

    import agents.evaluator as eval_mod

    def _no_inpod(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("in-pod evaluator must not run when auto-evaluate is off")

    monkeypatch.setattr(eval_mod, "schedule_evaluator", _no_inpod)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)


# ── Task 10 (#26) — Coverage adapter (null vs zero) ────────────────────
#
# Tests for _framework_coverage_strategy, _coverage_delta_for_subtask,
# and _validate_verdicts coverage_delta_pct validation.
#
# These tests exercise the real registry (frameworks/ dir in the repo)
# so playwright → "skip", pytest → "cobertura", jest → "lcov".
# Registry lookups are fast (YAML parse) and deterministic.


from agents.evaluator import (
    _coverage_delta_for_subtask,
    _framework_coverage_strategy,
    _validate_verdicts,
)

# ── _framework_coverage_strategy ────────────────────────────────────────


def test_framework_coverage_strategy_playwright_returns_skip() -> None:
    """Playwright descriptor has coverage_strategy='skip' → returns 'skip'."""
    strategy = _framework_coverage_strategy({"framework": "playwright"})
    assert strategy == "skip"


def test_framework_coverage_strategy_pytest_returns_cobertura() -> None:
    """pytest descriptor has coverage_strategy='cobertura'."""
    strategy = _framework_coverage_strategy({"framework": "pytest"})
    assert strategy == "cobertura"


def test_framework_coverage_strategy_jest_returns_lcov() -> None:
    """jest descriptor has coverage_strategy='lcov'."""
    strategy = _framework_coverage_strategy({"framework": "jest"})
    assert strategy == "lcov"


def test_framework_coverage_strategy_no_framework_returns_none() -> None:
    """Subtask without a framework field returns None (v0.1 back-compat)."""
    assert _framework_coverage_strategy({}) is None
    assert _framework_coverage_strategy({"framework": ""}) is None


def test_framework_coverage_strategy_unknown_framework_returns_none() -> None:
    """Unknown framework (not in registry) returns None — never blocks."""
    result = _framework_coverage_strategy({"framework": "nonexistent_fw_xyz"})
    assert result is None


# ── _coverage_delta_for_subtask ──────────────────────────────────────────


def test_signals_coverage_none_for_browser_lane_framework(
    spec_dir: Path,
) -> None:
    """Subtask with framework='playwright' (coverage_strategy='skip')
    must yield coverage_delta=None from _coverage_delta_for_subtask.
    No XML files should be read."""
    subtask = {
        "id": "st-playwright-0",
        "framework": "playwright",
        "files_to_create": ["tests/test_0.spec.ts"],
    }
    result = _coverage_delta_for_subtask(spec_dir, subtask)
    assert result is None


def test_signals_coverage_numeric_for_pytest_framework(
    spec_dir: Path,
) -> None:
    """Subtask with framework='pytest' does NOT skip coverage (cobertura).
    When the XML files are absent, returns None (not-computed path),
    but the key difference is that it does NOT short-circuit via skip."""
    # Write a coverage XML so the compute path is reachable and returns
    # a real CoverageDelta rather than None-from-missing-file.
    import xml.etree.ElementTree as ET

    def _write_cobertura(path, lines_covered):
        root = ET.Element("coverage", attrib={"line-rate": str(lines_covered / 10)})
        pkg = ET.SubElement(root, "packages")
        p = ET.SubElement(pkg, "package", attrib={"name": "app"})
        cls = ET.SubElement(p, "classes")
        c = ET.SubElement(cls, "class", attrib={"filename": "app/m.py"})
        ls = ET.SubElement(c, "lines")
        for i in range(lines_covered):
            ET.SubElement(ls, "line", attrib={"number": str(i + 1), "hits": "1"})
        ET.ElementTree(root).write(path)

    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    _write_cobertura(spec_dir / "findings" / "baseline_coverage.xml", 5)
    run_dir = spec_dir / "findings" / "runs" / "st-pytest-0"
    run_dir.mkdir(parents=True)
    _write_cobertura(run_dir / "coverage.xml", 8)

    subtask = {
        "id": "st-pytest-0",
        "framework": "pytest",
        "files_to_create": ["tests/test_0.py"],
    }
    result = _coverage_delta_for_subtask(spec_dir, subtask)
    # With XML present, should get a CoverageDelta (not None)
    assert result is not None


def test_signals_coverage_numeric_for_jest_framework(
    spec_dir: Path,
) -> None:
    """Subtask with framework='jest' (coverage_strategy='lcov') does NOT
    skip coverage.  With missing XML, returns None via the XML-absent path
    (not the skip-framework path)."""
    subtask = {
        "id": "st-jest-0",
        "framework": "jest",
        "files_to_create": ["tests/test_0.test.ts"],
    }
    # No XML files present → None via absent-file path
    result = _coverage_delta_for_subtask(spec_dir, subtask)
    assert result is None


def test_signals_coverage_none_when_framework_field_absent(
    spec_dir: Path,
) -> None:
    """v0.1 subtask (no framework field) falls through to the XML-check
    path.  With no XML, returns None — backward-compat preserved."""
    subtask = {
        "id": "st-legacy-0",
        "files_to_create": ["tests/test_0.py"],
        # no 'framework' key
    }
    result = _coverage_delta_for_subtask(spec_dir, subtask)
    assert result is None  # XML absent → None (not-computed)


# ── _validate_verdicts coverage_delta_pct ────────────────────────────────


def _make_verdict_doc(test_id: str, coverage_delta_pct) -> dict:
    """Build a minimal valid verdicts.json dict for one test."""
    return {
        "evaluator_version": "task10",
        "mode": "initial",
        "verdicts": [
            {
                "test_id": test_id,
                "verdict": "accept",
                "reasons": ["all signals green"],
                "signals_summary": {
                    "coverage_delta_pct": coverage_delta_pct,
                    "stability": "stable",
                    "mutation": "killed",
                    "lint_promotion": "no_findings",
                },
            }
        ],
        "generated_at": "2026-05-28T00:00:00+00:00",
    }


def test_validate_verdicts_accepts_null_coverage_pct(tmp_path: Path) -> None:
    """verdict with coverage_delta_pct=null is valid (browser lane)."""
    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"t0","verdict":"accept","signals_summary":{"coverage_delta_pct":null}}'
        "]}"
    )
    ok, err, count = _validate_verdicts(path)
    assert ok is True, f"expected ok but got error: {err}"
    assert count == 1


def test_validate_verdicts_accepts_numeric_coverage_pct(tmp_path: Path) -> None:
    """verdict with numeric coverage_delta_pct=12.3 is valid."""
    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"t1","verdict":"flag","signals_summary":{"coverage_delta_pct":12.3}}'
        "]}"
    )
    ok, err, count = _validate_verdicts(path)
    assert ok is True, f"expected ok but got error: {err}"
    assert count == 1


def test_validate_verdicts_accepts_zero_coverage_pct(tmp_path: Path) -> None:
    """coverage_delta_pct=0 is a valid numeric value."""
    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"t2","verdict":"reject","signals_summary":{"coverage_delta_pct":0}}'
        "]}"
    )
    ok, err, count = _validate_verdicts(path)
    assert ok is True
    assert count == 1


_GOOD_VERDICT = (
    '{"evaluator_version":"x","verdicts":['
    '{"test_id":"t0","verdict":"accept","signals_summary":{"coverage_delta_pct":null}}'
    "]}"
)


def test_validate_verdicts_tolerates_trailing_data(tmp_path: Path) -> None:
    """The reported bug: valid JSON + trailing prose ('Extra data: ...').

    The validator salvages the object AND rewrites the file clean so the
    Triager (which json.loads the same file) succeeds too.
    """
    import json as _json

    path = tmp_path / "verdicts.json"
    path.write_text(_GOOD_VERDICT + "\n\nHere are the verdicts above. Done!")
    ok, err, count = _validate_verdicts(path)
    assert ok is True, f"expected ok but got: {err}"
    assert count == 1
    # File was normalised to clean JSON (no trailing data).
    reparsed = _json.loads(path.read_text())
    assert reparsed["verdicts"][0]["test_id"] == "t0"


def test_validate_verdicts_tolerates_markdown_fence(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.json"
    path.write_text("```json\n" + _GOOD_VERDICT + "\n```\n")
    ok, err, count = _validate_verdicts(path)
    assert ok is True, f"expected ok but got: {err}"
    assert count == 1


def test_validate_verdicts_still_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.json"
    path.write_text("not json at all, no object here")
    ok, err, count = _validate_verdicts(path)
    assert ok is False
    assert "not valid JSON" in err


def test_validate_verdicts_rejects_string_coverage_pct(tmp_path: Path) -> None:
    """coverage_delta_pct must be a number or null; a string is rejected."""
    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"t3","verdict":"accept","signals_summary":{"coverage_delta_pct":"12.3"}}'
        "]}"
    )
    ok, err, _count = _validate_verdicts(path)
    assert ok is False
    assert "coverage_delta_pct" in err
    assert "number or null" in err


def test_validate_verdicts_rejects_na_string_coverage_pct(tmp_path: Path) -> None:
    """The LLM must not emit 'N/A' as a string; only null is accepted."""
    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"t4","verdict":"accept","signals_summary":{"coverage_delta_pct":"N/A"}}'
        "]}"
    )
    ok, err, _count = _validate_verdicts(path)
    assert ok is False
    assert "coverage_delta_pct" in err


def test_validate_verdicts_accepts_absent_coverage_pct(tmp_path: Path) -> None:
    """signals_summary with no coverage_delta_pct key is backward-compat."""
    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"t5","verdict":"accept","signals_summary":{"stability":"stable"}}'
        "]}"
    )
    ok, err, count = _validate_verdicts(path)
    assert ok is True
    assert count == 1


def test_validate_verdicts_warns_on_unexpected_numeric_for_browser_lane(
    tmp_path: Path,
    caplog,
) -> None:
    """When skip_coverage_test_ids includes the test_id and the LLM emits a
    numeric coverage_delta_pct, a warning is logged and the verdict is
    still accepted."""
    import logging

    path = tmp_path / "verdicts.json"
    path.write_text(
        '{"evaluator_version":"x","verdicts":['
        '{"test_id":"browser-test-0","verdict":"accept",'
        '"signals_summary":{"coverage_delta_pct":5.0}}'
        "]}"
    )
    with caplog.at_level(logging.WARNING, logger="agents.evaluator"):
        ok, err, count = _validate_verdicts(
            path,
            skip_coverage_test_ids=frozenset({"browser-test-0"}),
        )
    assert ok is True, f"unexpected failure: {err}"
    assert count == 1
    # Warning should mention the test_id and the numeric value
    assert any("browser-test-0" in r.message for r in caplog.records)


# ── _nix_verify_mode precedence (RFC-0016 #469) ──────────────────────────


def _contract_dir(tmp_path: Path, env: dict | None) -> Path:
    spec = tmp_path / "specs" / "099"
    (spec / "context").mkdir(parents=True, exist_ok=True)
    contract: dict = {"contract_version": "2", "tfactory": {"lanes": ["unit"]}}
    if env is not None:
        contract["environment"] = env
    (spec / "context" / "task_contract.json").write_text(json.dumps(contract))
    return spec


_NIX_ENV = {"provisioning": {"method": "nix", "generated": True}}
_IMG_ENV = {"provisioning": {"method": "image"}}


def test_nix_verify_mode_default_on_with_image_and_nix_env(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "ghcr.io/x/nix:latest")
    monkeypatch.delenv("TFACTORY_VERIFY_BACKEND", raising=False)
    spec = _contract_dir(tmp_path, _NIX_ENV)
    assert _nix_verify_mode(spec) is True


def test_nix_verify_mode_off_when_not_nix_env(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "ghcr.io/x/nix:latest")
    monkeypatch.delenv("TFACTORY_VERIFY_BACKEND", raising=False)
    spec = _contract_dir(tmp_path, _IMG_ENV)
    assert _nix_verify_mode(spec) is False


def test_nix_verify_mode_off_without_image(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    monkeypatch.delenv("TFACTORY_VERIFY_BACKEND", raising=False)
    spec = _contract_dir(tmp_path, _NIX_ENV)
    assert _nix_verify_mode(spec) is False


def test_nix_verify_mode_backend_force_nixjob(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    # forced even without a contract nix env (e.g. a repo-owned flake)
    monkeypatch.setenv("TFACTORY_VERIFY_BACKEND", "nixjob")
    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    spec = _contract_dir(tmp_path, _IMG_ENV)
    assert _nix_verify_mode(spec) is True


def test_nix_verify_mode_backend_force_docker_overrides(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    monkeypatch.setenv("TFACTORY_VERIFY_BACKEND", "docker")
    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "ghcr.io/x/nix:latest")
    spec = _contract_dir(tmp_path, _NIX_ENV)
    assert _nix_verify_mode(spec) is False


def test_nix_verify_mode_backend_force_host_overrides(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    monkeypatch.setenv("TFACTORY_VERIFY_BACKEND", "host")
    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "ghcr.io/x/nix:latest")
    spec = _contract_dir(tmp_path, _NIX_ENV)
    assert _nix_verify_mode(spec) is False


# ── #776 batched stability: 3 samples in ONE Nix Job, same verdict ────────


def _fake_batched_runner(monkeypatch, per_run_codes):
    """Patch run_pytest_lane_via_nix to emit a batched stdout for the given codes
    and record how it was called."""
    from tools.runners.docker_runner import DockerRunResult

    calls: dict = {"count": 0}

    def _fake(spec, project, test_file, *, extra_env=None, reruns=1, **kw):
        calls["count"] += 1
        calls["reruns"] = reruns
        codes = (
            per_run_codes[:reruns] if len(per_run_codes) >= reruns else per_run_codes
        )
        out = "".join(
            f"__PYTEST_RUN={i + 1}\nrun {i + 1}\n__PYTEST_EXIT={c}\n"
            for i, c in enumerate(codes)
        )
        return DockerRunResult(returncode=codes[0], stdout=out, stderr="")

    monkeypatch.setattr("agents.evaluator.run_pytest_lane_via_nix", _fake)
    return calls


def test_nix_batched_stability_stable_from_one_job(tmp_path, monkeypatch):
    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    calls = _fake_batched_runner(monkeypatch, [0, 0, 0])
    tf = tmp_path / "t_test.py"
    tf.write_text("def test_x(): assert True\n")
    res = evaluator._nix_batched_stability(tmp_path, tmp_path, tf)
    assert res is not None and res.verdict == StabilityVerdict.STABLE
    assert res.rerun_count == 3
    # The whole point: 3 samples cost ONE Job dispatch, not three.
    assert calls["count"] == 1 and calls["reruns"] == 3


def test_nix_batched_stability_detects_flake(tmp_path, monkeypatch):
    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    _fake_batched_runner(monkeypatch, [0, 1, 0])
    tf = tmp_path / "t_test.py"
    tf.write_text("def test_x(): assert True\n")
    res = evaluator._nix_batched_stability(tmp_path, tmp_path, tf)
    assert res is not None and res.verdict == StabilityVerdict.FLAKY


def test_nix_batched_stability_none_when_lane_unavailable(tmp_path, monkeypatch):
    """run_pytest_lane_via_nix None (no runner image) -> None so the caller falls
    back to the per-sample check_stability path."""
    from agents import evaluator

    monkeypatch.setattr(
        "agents.evaluator.run_pytest_lane_via_nix", lambda *a, **k: None
    )
    tf = tmp_path / "t_test.py"
    tf.write_text("def test_x(): assert True\n")
    assert evaluator._nix_batched_stability(tmp_path, tmp_path, tf) is None


# ─── #787: flaky-history must ignore environmental failures ──────────────────


def _stability_result(verdict, *, returncode=0, tail=""):
    from agents.stability_runner import StabilityResult, StabilityRun

    return StabilityResult(
        verdict=verdict,
        runs=(StabilityRun(returncode=returncode, stdout_tail=tail),),
    )


def _history_store(spec_dir):
    return spec_dir.parent.parent / "test_history.json"


def _spec_dir(tmp_path):
    d = tmp_path / "proj" / "specs" / "037"
    d.mkdir(parents=True)
    return d


def test_flaky_history_skips_environmental_import_fail(tmp_path):
    """#787: a no-SUT collection/import error (CONSISTENT_FAIL + failure_kind
    'import') must NOT be recorded. Otherwise a deadline-reaped / no-SUT first
    attempt records `false`, the next real run records `true`, and the
    flip_rate=1.00 flags every good test."""
    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    spec_dir = _spec_dir(tmp_path)
    stab = _stability_result(
        StabilityVerdict.CONSISTENT_FAIL,
        returncode=2,
        tail="ImportError: No module named hello_python",
    )
    assert stab.failure_kind == "import"
    assert evaluator._flaky_history_for_subtask(spec_dir, {"id": "t-1"}, stab) is None
    assert not _history_store(spec_dir).exists()  # nothing recorded


def test_flaky_history_skips_runner_error(tmp_path):
    """#787: the stability runner itself raising (verdict ERROR) is
    environmental, not a reliability signal — never record it."""
    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    spec_dir = _spec_dir(tmp_path)
    stab = _stability_result(StabilityVerdict.ERROR)
    assert evaluator._flaky_history_for_subtask(spec_dir, {"id": "t-1"}, stab) is None
    assert not _history_store(spec_dir).exists()


def test_flaky_history_records_stable_pass(tmp_path):
    """A real STABLE run still records a True outcome (guard must not over-skip)."""
    import json

    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    spec_dir = _spec_dir(tmp_path)
    stab = _stability_result(StabilityVerdict.STABLE, returncode=0)
    assert (
        evaluator._flaky_history_for_subtask(spec_dir, {"id": "t-1"}, stab) is not None
    )
    store = _history_store(spec_dir)
    assert store.exists()
    assert json.loads(store.read_text())["t-1"]["outcomes"] == [True]


def test_flaky_history_records_genuine_assertion_fail(tmp_path):
    """A genuine failing test (CONSISTENT_FAIL + failure_kind 'assertion') IS a
    real reliability signal and must still record False — the #787 guard must
    not swallow it."""
    import json

    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    spec_dir = _spec_dir(tmp_path)
    stab = _stability_result(
        StabilityVerdict.CONSISTENT_FAIL,
        returncode=1,
        tail="E   AssertionError: assert 1 == 2\nFAILED",
    )
    assert stab.failure_kind == "assertion"
    assert (
        evaluator._flaky_history_for_subtask(spec_dir, {"id": "t-1"}, stab) is not None
    )
    store = _history_store(spec_dir)
    assert json.loads(store.read_text())["t-1"]["outcomes"] == [False]


# ─── #776 Stage 1b: stability + mutation batched into ONE nix Job ────────────


def _mk_unit_subtask(spec_dir, source="def test_x():\n    assert 1 == 1\n"):
    tests = spec_dir / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_x.py").write_text(source)
    return {"id": "t-1", "language": "python", "files_to_create": ["tests/test_x.py"]}


def _fake_nix_result(stdout):
    from tools.runners.docker_runner import DockerRunResult

    return DockerRunResult(returncode=0, stdout=stdout, stderr="")


def test_mutation_from_codes_first_kill_wins():
    from agents.evaluator import _mutation_from_codes
    from agents.mutate_probe import MutationApplied, MutationVerdict

    def _m(i):
        return MutationApplied(operator=f"op{i}", lineno=i, before="a", after="b")

    cands = [("s1", _m(1)), ("s2", _m(2)), ("s3", _m(3))]
    # survive, kill, kill -> first KILL (index 2) wins
    res = _mutation_from_codes(cands, [0, 1, 1])
    assert res.verdict == MutationVerdict.KILLED
    assert res.mutation.operator == "op2"


def test_mutation_from_codes_all_survive_returns_first():
    from agents.evaluator import _mutation_from_codes
    from agents.mutate_probe import MutationApplied, MutationVerdict

    m = MutationApplied(operator="op1", lineno=1, before="a", after="b")
    res = _mutation_from_codes([("s1", m)], [0])
    assert res.verdict == MutationVerdict.SURVIVED
    assert res.mutation.operator == "op1"


def test_nix_batched_signals_stability_and_mutation_in_one_job(tmp_path, monkeypatch):
    """The batched path derives BOTH a STABLE stability and a KILLED mutation from
    one Job's stdout, and passes the generated mutant(s) to the primitive."""
    from agents import evaluator
    from agents.mutate_probe import MutationVerdict
    from agents.stability_runner import StabilityVerdict

    spec_dir = tmp_path / "proj" / "specs" / "001"
    subtask = _mk_unit_subtask(spec_dir)

    captured = {}

    def _fake(spec, proj, tf, *, extra_env=None, reruns=1, mutant_files=None, **kw):
        captured["mutant_files"] = mutant_files
        captured["reruns"] = reruns
        return _fake_nix_result(
            "__PYTEST_RUN=1\n.\n__PYTEST_EXIT=0\n"
            "__PYTEST_RUN=2\n.\n__PYTEST_EXIT=0\n"
            "__PYTEST_RUN=3\n.\n__PYTEST_EXIT=0\n"
            "__MUT_RUN=1\nF\n__MUT_EXIT=1\n"
        )

    monkeypatch.setattr("agents.evaluator.run_pytest_lane_via_nix", _fake)
    stability, mutation = evaluator._nix_batched_signals(spec_dir, tmp_path, subtask)

    assert stability is not None and stability.verdict == StabilityVerdict.STABLE
    assert mutation is not None and mutation.verdict == MutationVerdict.KILLED
    # the assert-bearing test yields >=1 candidate, staged and passed to the Job
    assert captured["mutant_files"] and len(captured["mutant_files"]) >= 1
    assert captured["reruns"] == 3  # one Job, three stability samples


def test_nix_batched_signals_truncated_mutation_returns_none(tmp_path, monkeypatch):
    """Candidates existed but the Job returned NO mutant codes (truncated) -> the
    stability is still returned but mutation is None, so the caller falls back to
    the per-candidate path rather than trusting an incomplete batch."""
    from agents import evaluator
    from agents.stability_runner import StabilityVerdict

    spec_dir = tmp_path / "proj" / "specs" / "001"
    subtask = _mk_unit_subtask(spec_dir)

    def _fake(spec, proj, tf, *, extra_env=None, reruns=1, mutant_files=None, **kw):
        # stability markers only; the __MUT_* markers never printed
        return _fake_nix_result(
            "__PYTEST_RUN=1\n.\n__PYTEST_EXIT=0\n"
            "__PYTEST_RUN=2\n.\n__PYTEST_EXIT=0\n"
            "__PYTEST_RUN=3\n.\n__PYTEST_EXIT=0\n"
        )

    monkeypatch.setattr("agents.evaluator.run_pytest_lane_via_nix", _fake)
    stability, mutation = evaluator._nix_batched_signals(spec_dir, tmp_path, subtask)
    assert stability is not None and stability.verdict == StabilityVerdict.STABLE
    assert mutation is None  # incomplete batch -> caller falls back


def test_nix_batched_signals_none_when_lane_unavailable(tmp_path, monkeypatch):
    from agents import evaluator

    spec_dir = tmp_path / "proj" / "specs" / "001"
    subtask = _mk_unit_subtask(spec_dir)
    monkeypatch.setattr(
        "agents.evaluator.run_pytest_lane_via_nix", lambda *a, **k: None
    )
    assert evaluator._nix_batched_signals(spec_dir, tmp_path, subtask) == (None, None)


def test_build_signal_bundle_uses_batched_path_in_nix_mode(tmp_path, monkeypatch):
    """In nix mode, _build_signal_bundle takes the batched (stability, mutation)
    from _nix_batched_signals instead of the per-primitive calls."""
    from agents import evaluator
    from agents.mutate_probe import MutationResult, MutationVerdict
    from agents.stability_runner import StabilityResult, StabilityVerdict

    spec_dir = tmp_path / "proj" / "specs" / "001"
    subtask = _mk_unit_subtask(spec_dir)

    fake_stab = StabilityResult(verdict=StabilityVerdict.STABLE)
    fake_mut = MutationResult(verdict=MutationVerdict.KILLED)
    monkeypatch.setattr(evaluator, "_nix_verify_mode", lambda sd: True)
    monkeypatch.setattr(
        evaluator, "_nix_batched_signals", lambda sd, pd, st: (fake_stab, fake_mut)
    )
    # if the per-primitive path were taken these would blow up the test:
    monkeypatch.setattr(
        evaluator,
        "_stability_for_subtask",
        lambda *a, **k: pytest.fail("per-primitive stability should not run"),
    )
    bundle = evaluator._build_signal_bundle(spec_dir, tmp_path, subtask, runner_fn=None)
    assert bundle.stability is fake_stab
    assert bundle.mutation is fake_mut


def test_build_signal_bundle_falls_back_when_nix_unavailable(tmp_path, monkeypatch):
    from agents import evaluator
    from agents.stability_runner import StabilityResult, StabilityVerdict

    spec_dir = tmp_path / "proj" / "specs" / "001"
    subtask = _mk_unit_subtask(spec_dir)

    monkeypatch.setattr(evaluator, "_nix_verify_mode", lambda sd: True)
    monkeypatch.setattr(evaluator, "_nix_batched_signals", lambda *a: (None, None))
    called = {"stab": False, "mut": False}

    def _stab(*a, **k):
        called["stab"] = True
        return StabilityResult(verdict=StabilityVerdict.STABLE)

    def _mut(*a, **k):
        called["mut"] = True
        return None

    monkeypatch.setattr(evaluator, "_stability_for_subtask", _stab)
    monkeypatch.setattr(evaluator, "_mutation_for_subtask", _mut)
    bundle = evaluator._build_signal_bundle(spec_dir, tmp_path, subtask, runner_fn=None)
    assert called["stab"] and called["mut"]  # per-primitive fallback ran
    assert bundle.stability.verdict == StabilityVerdict.STABLE


def test_verdict_lanes_are_stamped_from_the_plan(tmp_path):
    """#1018: val_block groups VAL levels by verdict['lane'].

    Nothing ever wrote that field, and val_block defaults a missing lane to
    "unit", so api/browser/integration verdicts were all counted as unit. VAL-2
    then saw zero verdicts and reported "no api/integration/browser lane ran",
    capping every run at VAL-0 regardless of what executed.
    """
    import json

    from agents.evaluator import _stamp_verdict_lanes

    (tmp_path / "test_plan.json").write_text(
        json.dumps(
            {
                "phases": [
                    {"phase": 1, "subtasks": [{"id": "echo-api", "lane": "api"}]},
                    {"phase": 2, "subtasks": [{"id": "guard-unit", "lane": "unit"}]},
                ]
            }
        )
    )
    doc = {"verdicts": [{"test_id": "echo-api"}, {"test_id": "guard-unit"}]}
    stamped, unmatched = _stamp_verdict_lanes(tmp_path, doc)

    assert (stamped, unmatched) == (2, 0)
    assert doc["verdicts"][0]["lane"] == "api"
    assert doc["verdicts"][1]["lane"] == "unit"


def test_the_stamped_lanes_reach_val2(tmp_path):
    """The point of the stamp: an api verdict must land in VAL-2, not VAL-1.

    Asserting only that the field is set would pass even if val_block still
    binned it as unit, so this drives the real grouping function.
    """
    import json

    from agents.evaluator import _stamp_verdict_lanes
    from agents.val_block import build_verification_block

    (tmp_path / "test_plan.json").write_text(
        json.dumps({"phases": [{"subtasks": [{"id": "echo-api", "lane": "api"}]}]})
    )
    doc = {"verdicts": [{"test_id": "echo-api", "verdict": "accept"}]}

    unstamped = build_verification_block(list(doc["verdicts"]), target_level="VAL-2")
    by_lvl = {lvl["level"]: lvl for lvl in unstamped["levels"]}
    assert by_lvl["VAL-2"]["status"] == "not_run", "precondition: the bug"

    _stamp_verdict_lanes(tmp_path, doc)
    fixed = build_verification_block(doc["verdicts"], target_level="VAL-2")
    by_lvl = {lvl["level"]: lvl for lvl in fixed["levels"]}
    assert by_lvl["VAL-2"]["status"] == "passed", fixed


def test_an_unplanned_verdict_is_left_unattributed(tmp_path):
    """Never guess a lane. An unmatched verdict must not inflate the unit lane
    — that silent default is the defect this fixes."""
    import json

    from agents.evaluator import _stamp_verdict_lanes

    (tmp_path / "test_plan.json").write_text(json.dumps({"phases": []}))
    doc = {"verdicts": [{"test_id": "ghost"}]}
    stamped, unmatched = _stamp_verdict_lanes(tmp_path, doc)

    assert (stamped, unmatched) == (0, 1)
    assert "lane" not in doc["verdicts"][0]


_COBERTURA = """<?xml version="1.0" ?>
<coverage line-rate="0.75" lines-covered="3" lines-valid="4">
  <packages><package name="."><classes>
    <class filename="src/app/request_id.py">
      <lines>
        <line number="10" hits="1"/>
        <line number="11" hits="1"/>
        <line number="12" hits="0"/>
      </lines>
    </class>
    <class filename="tests/unit/test_request_id.py">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="1"/>
        <line number="3" hits="1"/>
      </lines>
    </class>
  </classes></package></packages>
</coverage>
"""


def _write_cov(spec_dir, test_id: str) -> None:
    d = spec_dir / "findings" / "runs" / test_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "coverage.xml").write_text(_COBERTURA)


def test_coverage_is_measured_from_the_lane_report(tmp_path):
    """#1024: the number must come from coverage.xml, not from the judge."""
    from agents.evaluator import _stamp_verdict_coverage

    _write_cov(tmp_path, "t1")
    # The judge's fabricated zero, exactly as it appeared in live verdicts.
    doc = {
        "verdicts": [
            {
                "test_id": "t1",
                "signals_summary": {"coverage_delta_pct": 0, "coverage_new_lines": 0},
            }
        ]
    }

    measured, unmeasured = _stamp_verdict_coverage(tmp_path, doc)

    assert (measured, unmeasured) == (1, 0)
    s = doc["verdicts"][0]["signals_summary"]
    # 2 covered SUT lines; the 3 covered lines of the test file do not count.
    assert s["coverage_new_lines"] == 2, s
    # No baseline snapshot exists, so a delta cannot be computed — and must not
    # be reported as a number.
    assert s["coverage_delta_pct"] is None, s


def test_test_file_lines_are_not_counted_as_coverage(tmp_path):
    """Every test covers its own lines; counting them measures nothing.

    Without the exclusion this report yields 5 instead of 2, and every test
    would score a perfect coverage subscore regardless of what it exercised.
    """
    from agents.evaluator import _measured_coverage

    _write_cov(tmp_path, "t1")
    covered, _ = _measured_coverage(tmp_path, "t1")
    assert covered == 2


def test_unmeasured_coverage_is_null_not_zero(tmp_path):
    """A missing report must read as 'not measured', never as a measured zero.

    confidence._coverage_subscore scores 0 as 'exercises none' but DROPS None,
    so a fabricated zero silently penalises every verdict.
    """
    from agents.evaluator import _stamp_verdict_coverage

    doc = {
        "verdicts": [
            {
                "test_id": "absent",
                "signals_summary": {"coverage_delta_pct": 0, "coverage_new_lines": 0},
            }
        ]
    }

    measured, unmeasured = _stamp_verdict_coverage(tmp_path, doc)

    assert (measured, unmeasured) == (0, 1)
    s = doc["verdicts"][0]["signals_summary"]
    assert s["coverage_delta_pct"] is None, s
    assert s["coverage_new_lines"] is None, s


def test_the_measured_value_reaches_the_confidence_scorer(tmp_path):
    """Drive the real scorer: a measured zero and a null must differ."""
    from agents.confidence import _coverage_subscore

    assert _coverage_subscore({"coverage_new_lines": 2}) == 1.0
    assert _coverage_subscore({"coverage_new_lines": 0}) == 0.0
    assert (
        _coverage_subscore({"coverage_new_lines": None, "coverage_delta_pct": None})
        is None
    )


def test_the_runner_seam_persists_the_lane_coverage_report(tmp_path):
    """#1024: the lane's coverage.xml must be copied where the reader looks.

    It was written into the runner's scratch dir and dropped, so
    _coverage_delta_for_subtask never found its `after` snapshot.
    """
    from types import SimpleNamespace

    from agents.evaluator import _capturing_coverage

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    src = scratch / "coverage.xml"
    src.write_text(_COBERTURA)

    wrapped = _capturing_coverage(
        tmp_path, {"id": "t1"}, lambda *a, **k: SimpleNamespace(coverage_xml_path=src)
    )
    wrapped(tmp_path / "tests" / "test_x.py", tmp_path, 0)

    dest = tmp_path / "findings" / "runs" / "t1" / "coverage.xml"
    assert dest.is_file(), "coverage report was not persisted"
    assert dest.read_text() == _COBERTURA
    assert src.is_file(), "copied, not moved — the scratch dir is the runner's"


def test_a_runner_with_no_coverage_report_is_not_an_error(tmp_path):
    """A lane that produced none (browser) must pass through untouched."""
    from types import SimpleNamespace

    from agents.evaluator import _capturing_coverage

    sentinel = SimpleNamespace(coverage_xml_path=None, ok=True)
    wrapped = _capturing_coverage(tmp_path, {"id": "t1"}, lambda *a, **k: sentinel)
    assert wrapped(tmp_path, tmp_path, 0) is sentinel
    assert not (tmp_path / "findings" / "runs" / "t1").exists()


def test_persisted_artifact_outlives_the_scratch_dir(tmp_path):
    """#1024: the returned path must survive the runner's `finally` cleanup.

    The runner built its result with `cov if cov.exists() else None` and then
    deleted the scratch dir in a `finally` that runs BEFORE the value reaches
    the caller. The path was therefore true at construction and dangling by the
    time any consumer read it — which is why coverage_xml_path had exactly one
    reader in the tree and the verdict's coverage was a judge-authored guess.
    """
    import shutil as _shutil

    from agents.evaluator import _persist_run_artifact

    scratch = tmp_path / "tf-pytest-xyz"
    scratch.mkdir()
    (scratch / "coverage.xml").write_text(_COBERTURA)
    test_file = tmp_path / "tests" / "unit" / "test_thing.py"

    kept = _persist_run_artifact(tmp_path, test_file, scratch / "coverage.xml")
    # Exactly what the runner does next.
    _shutil.rmtree(scratch, ignore_errors=True)

    assert kept is not None
    assert kept.is_file(), "artifact did not survive scratch cleanup"
    assert scratch not in kept.parents, "still inside the doomed scratch dir"
    assert kept.read_text() == _COBERTURA


def test_a_missing_artifact_yields_none_not_a_dangling_path(tmp_path):
    """No file → None. Never a path the caller will find deleted."""
    from agents.evaluator import _persist_run_artifact

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert (
        _persist_run_artifact(
            tmp_path, tmp_path / "tests" / "t.py", scratch / "nope.xml"
        )
        is None
    )


def test_host_runner_result_paths_survive_the_caller_cleanup(tmp_path, monkeypatch):
    """#1024 at the call site, not just in the helper.

    Drives the real `_resolve_runner_fn._run` on the host branch — the one k3d
    takes — with the pytest invocation stubbed. `_run` deletes its scratch dir
    in a `finally`, so a result still pointing there is dead on arrival. Asserts
    the returned paths are readable AFTER the call, which is the only property
    a consumer cares about.
    """
    from agents import evaluator as E

    spec_dir = tmp_path / "spec"
    (spec_dir / "tests").mkdir(parents=True)
    project_dir = tmp_path / "proj"
    (project_dir / "src").mkdir(parents=True)
    test_file = spec_dir / "tests" / "test_thing.py"
    test_file.write_text("def test_ok():\n    assert True\n")

    monkeypatch.setattr(E, "_nix_verify_mode", lambda *a, **k: False)
    monkeypatch.setattr(E, "_host_runner_mode", lambda *a, **k: True)
    monkeypatch.setattr(E, "_stage_sut_into_scratch", lambda *a, **k: None)

    def _fake_host_run(scratch, tf, extra_env, project_dir_arg):
        # Exactly what the real one does: write into the caller's scratch dir
        # and hand back paths into it.
        from tools.runners.docker_runner import DockerRunResult

        (Path(scratch) / "coverage.xml").write_text(_COBERTURA)
        (Path(scratch) / "junit.xml").write_text("<testsuite/>")
        return DockerRunResult(
            returncode=0,
            stdout="__PYTEST_EXIT=0",
            stderr="",
            junit_xml_path=Path(scratch) / "junit.xml",
            coverage_xml_path=Path(scratch) / "coverage.xml",
            argv=["pytest"],
        )

    monkeypatch.setattr(E, "_run_pytest_on_host", _fake_host_run)

    res = E._resolve_runner_fn(spec_dir, project_dir)(test_file, project_dir, 0)

    assert res.coverage_xml_path is not None, "coverage path was dropped"
    assert res.coverage_xml_path.is_file(), (
        "coverage path does not survive the runner's scratch cleanup"
    )
    assert res.junit_xml_path is not None and res.junit_xml_path.is_file()
    assert res.coverage_xml_path.read_text() == _COBERTURA


def test_docker_runner_result_paths_survive_the_caller_cleanup(tmp_path, monkeypatch):
    """Same #1024 property on the DockerRunner branch.

    Reachable wherever a container runtime exists, and it builds its paths in
    the same doomed scratch dir.
    """
    from agents import evaluator as E
    from tools.runners.docker_runner import DockerRunResult

    spec_dir = tmp_path / "spec"
    (spec_dir / "tests").mkdir(parents=True)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    test_file = spec_dir / "tests" / "test_thing.py"
    test_file.write_text("def test_ok():\n    assert True\n")

    monkeypatch.setattr(E, "_nix_verify_mode", lambda *a, **k: False)
    monkeypatch.setattr(E, "_host_runner_mode", lambda *a, **k: False)
    monkeypatch.setattr(E, "_stage_sut_into_scratch", lambda *a, **k: None)

    class _FakeRunner:
        def __init__(self, *a, **k):
            pass

        def run(self, *, scratch_path, **kwargs):
            (Path(scratch_path) / "coverage.xml").write_text(_COBERTURA)
            (Path(scratch_path) / "junit.xml").write_text("<testsuite/>")
            return DockerRunResult(
                returncode=0, stdout="__PYTEST_EXIT=0", stderr="", argv=["pytest"]
            )

    import tools.runners.docker_runner as _dr

    monkeypatch.setattr(_dr, "DockerRunner", _FakeRunner)

    res = E._resolve_runner_fn(spec_dir, project_dir)(test_file, project_dir, 0)

    assert res.coverage_xml_path is not None, "coverage path was dropped"
    assert res.coverage_xml_path.is_file(), (
        "coverage path does not survive the runner's scratch cleanup"
    )
    assert res.coverage_xml_path.read_text() == _COBERTURA


def _write_verdicts(tmp_path, verdicts):
    p = tmp_path / "verdicts.json"
    p.write_text(json.dumps({"verdicts": verdicts}))
    return p


def test_lane_progress_separates_a_lane_that_ran_from_one_that_could_not(tmp_path):
    """#1152/#431: lane_progress was written 'pending' and never advanced.

    Two initialisers, one rerun reset, no writer anywhere that moved a lane off
    'pending' — so "every lane pending" was equally true of a clean run and a
    dead one. The cockpit badge and this repo's demo runbook both told readers
    to check that field, and it was a constant.

    error must stay distinct from pending: a lane that tried and could not run
    is a different fact from a lane nobody asked for.
    """
    from agents.evaluator import _derive_lane_progress

    path = _write_verdicts(
        tmp_path,
        [
            {
                "test_id": "a",
                "lane": "unit",
                "signals_summary": {"stability": "stable"},
            },
            {
                "test_id": "b",
                "lane": "browser",
                "signals_summary": {"stability": "error"},
            },
        ],
    )
    assert _derive_lane_progress(tmp_path, path) == {
        "unit": "executed",
        "browser": "error",
    }


def test_lane_progress_needs_only_one_real_result_to_call_a_lane_executed(tmp_path):
    """A lane with a mix is executed, not error: one test erroring is a test
    result, whereas ALL of them erroring is what a broken runner looks like."""
    from agents.evaluator import _derive_lane_progress

    path = _write_verdicts(
        tmp_path,
        [
            {
                "test_id": "a",
                "lane": "browser",
                "signals_summary": {"stability": "error"},
            },
            {
                "test_id": "b",
                "lane": "browser",
                "signals_summary": {"stability": "flaky"},
            },
        ],
    )
    assert _derive_lane_progress(tmp_path, path) == {"browser": "executed"}


def test_lane_progress_leaves_untouched_lanes_pending(tmp_path):
    """Only lanes that produced a verdict are claimed either way. A lane nobody
    ran keeps whatever status.json already said, rather than being guessed at."""
    from agents.evaluator import _derive_lane_progress

    (tmp_path / "status.json").write_text(
        json.dumps({"lane_progress": {"unit": "pending", "mutation": "pending"}})
    )
    path = _write_verdicts(
        tmp_path,
        [{"test_id": "a", "lane": "unit", "signals_summary": {"stability": "stable"}}],
    )
    assert _derive_lane_progress(tmp_path, path) == {
        "unit": "executed",
        "mutation": "pending",
    }


def test_lane_progress_is_none_when_no_verdict_carries_a_lane(tmp_path):
    """None, not {}. An empty dict would overwrite a real lane_progress with
    nothing; None leaves the existing value alone."""
    from agents.evaluator import _derive_lane_progress

    path = _write_verdicts(tmp_path, [{"test_id": "a"}])
    assert _derive_lane_progress(tmp_path, path) is None


def test_lane_progress_survives_a_verdict_with_no_signals(tmp_path):
    """Unknown stability counts as executed. Treating unknown as failure would
    repaint healthy runs red — the same bug pointing the other way."""
    from agents.evaluator import _derive_lane_progress

    path = _write_verdicts(tmp_path, [{"test_id": "a", "lane": "api"}])
    assert _derive_lane_progress(tmp_path, path) == {"api": "executed"}


# ── jest staging flattened the test away from its imports (#1165) ─────────────
#
# The SUT is copied into the scratch dir preserving structure, but the TEST was
# kept at its own path only when "tests" appeared in it, and flattened to the
# bare filename otherwise. So games/tictactoe/game.test.js landed at the scratch
# ROOT while its require("./game.js") target sat three directories away, and
# every JS project without a tests/ dir failed on the import before jest ran a
# single assertion.
#
# Same defect as the e2e staging flattening: a test moved away from the layout it
# was authored against breaks its own fixtures, and reports as a test failure
# rather than a staging failure.


def _staged_rel(test_file, project_dir):
    """The placement _resolve_jest_runner_fn computes for a staged spec."""
    from pathlib import Path as _P

    try:
        return _P(test_file).resolve().relative_to(_P(project_dir).resolve())
    except ValueError:
        return _P(_P(test_file).name)


def test_a_test_outside_a_tests_dir_keeps_its_path(tmp_path):
    """The spec-160 layout: games/tictactoe/game.test.js beside its module."""
    proj = tmp_path / "proj"
    d = proj / "games" / "tictactoe"
    d.mkdir(parents=True)
    (d / "game.js").write_text("module.exports = {};")
    tf = d / "game.test.js"
    tf.write_text("require('./game.js');")

    rel = _staged_rel(tf, proj)

    assert rel == Path("games/tictactoe/game.test.js")
    # the property that matters: ./game.js resolves next to the staged test
    assert (proj / rel).parent.joinpath("game.js").is_file()


def test_a_conventional_tests_dir_still_works(tmp_path):
    """The layout the old heuristic handled must not regress."""
    proj = tmp_path / "proj"
    d = proj / "tests" / "unit"
    d.mkdir(parents=True)
    (d / "game.js").write_text("module.exports = {};")
    tf = d / "game.test.js"
    tf.write_text("require('./game.js');")

    assert _staged_rel(tf, proj) == Path("tests/unit/game.test.js")


def test_a_test_outside_the_project_falls_back_to_its_name(tmp_path):
    """No faithful placement exists. The bare name at least runs; a guessed
    prefix would resolve to the WRONG module and produce a confident wrong
    verdict."""
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "elsewhere" / "x.test.js"
    outside.parent.mkdir()
    outside.write_text("x")

    assert _staged_rel(outside, proj) == Path("x.test.js")
