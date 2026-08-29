"""Tests for the real TFactory Gen-Functional agent — Task 6 (#7 v0.1 / #22 v0.2).

Real ``run_gen_functional`` invokes the Claude Agent SDK; these tests
mock the two SDK seams (``_resolve_client`` + ``_invoke_session``) so
we exercise the orchestration without burning tokens. The two
guardrails (preflight_static + flake_risk_lint) are NOT mocked —
they run for real because they're cheap (subprocess preflight ≤ 1s,
AST flake-lint ≤ 1ms) and that gives better signal.

Covered (v0.1):
  - Happy single-subtask: SDK writes a valid test → both guards pass →
    subtask completed, status=generated, tests_generated=1
  - Happy multi-subtask: all three pass → tests_generated=3
  - Agent didn't write the file → replan_request emitted, status=
    replan_needed, Planner replan auto-scheduled
  - Pre-flight rejects hallucinated import → replan_request, replan_needed
  - Flake-lint rejects dict-iteration assertion → replan_request, replan_needed
  - Session error → subtask marked failed, loop continues
  - No pending subtasks → generated_empty (warning, not failure)
  - Plan missing → gen_functional_failed
  - schedule_gen_functional env gating + GC anchor
  - Full chain: planner success → gen_functional → guardrail rejects →
    planner replan auto-fires

Covered (v0.2 / Task 6 #22):
  - _resolve_framework_descriptor: pytest/jest/playwright lookups
  - _resolve_framework_descriptor: None for v0.1-style (framework=None)
  - _resolve_framework_descriptor: unknown framework → LookupError
  - _resolve_runner_fn: image parameterized by descriptor
  - _resolve_runner_fn: legacy fallback + DeprecationWarning
  - run_gen_functional dispatches jest/playwright subtasks (mocked SDK)
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from agents.gen_functional import (
    _BG_GEN_FUNCTIONAL_TASKS,
    _resolve_framework_descriptor,
    _resolve_runner_fn,
    run_gen_functional,
    schedule_gen_functional,
)

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "planner_smoke" / "project_tree"


# ── autouse: keep the planner-replan auto-fire deterministic ────────────


@pytest.fixture(autouse=True)
def _disable_planner_auto_replan(monkeypatch: pytest.MonkeyPatch) -> None:
    """gen_functional rejections schedule the planner in replan mode;
    gen_functional successes schedule the evaluator. Pin both env vars
    OFF so the autouse default is fully deterministic. Individual chain
    tests opt back in explicitly."""
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    """Workspace post-planner. Plan written with subtasks targeting the
    fixture project (app.auth)."""
    d = tmp_path / "workspaces" / "demo" / "specs" / "001"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(
        json.dumps(
            {
                "task_id": "001",
                "project_id": "demo",
                "status": "planned",
                "phase": "planner_initial_complete",
            }
        )
    )
    return d


@pytest.fixture
def project_dir() -> Path:
    return FIXTURE_PROJECT


def _make_plan(spec_dir: Path, subtask_count: int = 1) -> None:
    """Write a test_plan.json with N pending functional subtasks targeting
    the fixture project's app.auth.login_user."""
    plan = {
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "AC#1",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": f"s{i}",
                        "description": f"test {i}",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1",
                        "files_to_create": [f"tests/test_s{i}.py"],
                        "verification": {
                            "type": "command",
                            "run": f"pytest tests/test_s{i}.py",
                        },
                    }
                    for i in range(subtask_count)
                ],
                "parallel_safe": False,
            }
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    }
    (spec_dir / "test_plan.json").write_text(json.dumps(plan))


def _valid_test_source() -> str:
    """A test source the guards should accept:
    - imports a real symbol from the fixture project
    - no flake-risk patterns
    """
    return (
        "from app.auth import login_user\n"
        "\n"
        "def test_login_user_exists():\n"
        "    assert callable(login_user)\n"
    )


def _hallucinated_import_source() -> str:
    """Pre-flight will reject this — `app.auth.totally_fake_func` doesn't exist."""
    return (
        "from app.auth import totally_fake_func_xyz\n"
        "\n"
        "def test_x():\n"
        "    assert totally_fake_func_xyz() is not None\n"
    )


def _flaky_dict_source() -> str:
    """Flake-lint will reject this (dict_iteration_order, high severity)."""
    return (
        "def test_x():\n    d = {1: 'a', 2: 'b'}\n    assert list(d.keys()) == [1, 2]\n"
    )


@pytest.fixture
def mock_sdk(monkeypatch: pytest.MonkeyPatch):
    """Patch the two SDK seams in agents.gen_functional.

    Caller passes a callable that gets the spec_dir + the subtask
    being processed and returns the test source to "write" (or None
    to simulate the agent not calling Write). Optionally returns a
    custom session status."""
    call_log: list[dict] = []

    class _FakeAsyncCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    def _setup(*, source_for, status_for=None):
        """source_for: callable(subtask_id) → str | None
        status_for: callable(subtask_id) → "complete" | "error". Default complete.
        """

        async def _resolve(*a, **kw):
            return _FakeAsyncCM()

        async def _invoke(client, prompt, spec_dir_arg, verbose):
            # Best-effort: extract the subtask_id from the prompt's
            # SUBTASK CONTEXT block so the test mock can dispatch.
            subtask_id = "?"
            for line in prompt.splitlines():
                if line.startswith("Subtask: `") and "` —" in line:
                    subtask_id = line.split("`")[1]
                    break
            call_log.append({"subtask_id": subtask_id})

            src = source_for(subtask_id) if source_for else None
            if src is not None:
                # Locate the Write path from the prompt's SUBTASK CONTEXT.
                write_path = None
                for line in prompt.splitlines():
                    if line.startswith("- write the file at:"):
                        # Format: "- write the file at: `/path/to/file`"
                        write_path = line.split("`")[1]
                        break
                if write_path:
                    p = Path(write_path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(src)

            status = status_for(subtask_id) if status_for else "complete"
            return status, "mock response", {}

        monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
        monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)
        return call_log

    return _setup


# ── Happy paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_single_subtask(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: _valid_test_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"
    assert status["tests_generated"] == 1
    assert (spec_dir / "tests" / "test_s0.py").exists()


@pytest.mark.asyncio
async def test_happy_multi_subtask(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    _make_plan(spec_dir, subtask_count=3)
    mock_sdk(source_for=lambda sid: _valid_test_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["tests_generated"] == 3
    for i in range(3):
        assert (spec_dir / "tests" / f"test_s{i}.py").exists()


@pytest.mark.asyncio
async def test_heartbeat_bumps_status_each_subtask(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
    monkeypatch,
) -> None:
    """Each subtask emits a heartbeat status patch so a long multi-subtask run
    keeps its updated_at fresh and the #95 watchdog can't false-stall a healthy
    generation (#742/#774)."""
    _make_plan(spec_dir, subtask_count=3)
    mock_sdk(source_for=lambda sid: _valid_test_source())

    import agents.gen_functional as gf

    phases: list = []
    orig = gf._write_status_patch

    def _spy(sd, **fields):
        phases.append(fields.get("phase"))
        return orig(sd, **fields)

    monkeypatch.setattr(gf, "_write_status_patch", _spy)

    assert await run_gen_functional(spec_dir, project_dir) is True
    heartbeats = [p for p in phases if p and p.startswith("gen_functional_subtask_")]
    assert heartbeats == [
        "gen_functional_subtask_1_of_3",
        "gen_functional_subtask_2_of_3",
        "gen_functional_subtask_3_of_3",
    ]


@pytest.mark.asyncio
async def test_happy_marks_subtasks_completed(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    _make_plan(spec_dir, subtask_count=2)
    mock_sdk(source_for=lambda sid: _valid_test_source())

    await run_gen_functional(spec_dir, project_dir)

    plan = json.loads((spec_dir / "test_plan.json").read_text())
    statuses = {s["id"]: s["status"] for s in plan["phases"][0]["subtasks"]}
    assert statuses == {"s0": "completed", "s1": "completed"}


# ── Guardrail rejections → replan_request ───────────────────────────────


@pytest.mark.asyncio
async def test_agent_didnt_write_triggers_replan(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: None)  # mock skips Write

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "replan_needed"
    assert "no_write" in status["phase"]
    assert status["last_rejected_subtask"] == "s0"

    rr_path = spec_dir / "context" / "replan_request.json"
    assert rr_path.exists()
    rr = json.loads(rr_path.read_text())
    assert rr["subtask_id"] == "s0"
    assert "did not Write" in rr["reason"]
    assert rr["failed_target"] == "app/auth/login.py::login_user"


@pytest.mark.asyncio
async def test_preflight_rejection_triggers_replan(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """Hallucinated import → real preflight check rejects → replan."""
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: _hallucinated_import_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "replan_needed"
    assert "preflight" in status["phase"]

    rr = json.loads((spec_dir / "context" / "replan_request.json").read_text())
    assert "pre-flight rejected" in rr["reason"]
    # The bad test file got cleaned up
    assert not (spec_dir / "tests" / "test_s0.py").exists()


@pytest.mark.asyncio
async def test_flake_lint_rejection_triggers_replan(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """dict iteration order → flake-lint high-sev → replan."""
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: _flaky_dict_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "replan_needed"
    assert "flake" in status["phase"]

    rr = json.loads((spec_dir / "context" / "replan_request.json").read_text())
    assert "flake-lint rejected" in rr["reason"]
    assert not (spec_dir / "tests" / "test_s0.py").exists()


@pytest.mark.asyncio
async def test_first_rejection_stops_loop(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """Three pending subtasks; first one is bad → loop stops + replan."""
    _make_plan(spec_dir, subtask_count=3)
    calls = mock_sdk(source_for=lambda sid: _hallucinated_import_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False
    # Only one SDK invocation happened (the loop stopped on the first reject)
    assert len(calls) == 1
    # Only s0's replan got requested
    rr = json.loads((spec_dir / "context" / "replan_request.json").read_text())
    assert rr["subtask_id"] == "s0"


def _submodule_import_source() -> str:
    """#712: a healthy test importing a real SUBMODULE the package __init__ does
    not re-export (``app/__init__.py`` is empty, so ``auth`` is a submodule, not
    an attribute). This resolves in the real test run but, before the #712 fix,
    pre-flight false-rejected it with 'app has no attribute auth' → replan →
    STUCK budget → nothing committed → no VAL verdict (the residual root cause of
    #707/#712, not covered by the #709 absent-*module* fix)."""
    return (
        "from app import auth\n"
        "\n"
        "def test_auth_module_importable():\n"
        "    assert hasattr(auth, 'login_user')\n"
    )


@pytest.mark.asyncio
async def test_submodule_import_commits_instead_of_replanning(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """#712 regression: a test importing a real (non-re-exported) submodule now
    COMMITS instead of replan-looping to budget. Guards against the pre-flight
    submodule false-reject."""
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: _submodule_import_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"
    assert status["tests_generated"] == 1
    # The test file is committed (not deleted by a guardrail rejection), and no
    # replan reason accumulated.
    assert (spec_dir / "tests" / "test_s0.py").exists()
    assert not (spec_dir / "context" / "replan_request.json").exists()
    assert status.get("replan_reasons") in (None, [])


# ── Criterion-literal drift (#888) ─────────────────────────────────────
#
# The generator rewrote a signed acceptance criterion to match the
# implementation and reported the rewrite as verified. These two tests pin both
# directions through the real orchestration: the rewrite is rejected and routed
# to a human, and the faithful version commits untouched.

_AC3_DESCRIPTION = (
    "Verify POST /api/line-total with unit_price 10.00, quantity 1, "
    "vat_rate 0.175 returns 200 with net 10.00, vat 1.75, total 11.76."
)


def _set_description(spec_dir: Path, description: str) -> None:
    plan = json.loads((spec_dir / "test_plan.json").read_text())
    plan["phases"][0]["subtasks"][0]["description"] = description
    (spec_dir / "test_plan.json").write_text(json.dumps(plan))


def _rewritten_criterion_source() -> str:
    """11.76 only in comments; the assertion carries the implementation's 11.75.

    Shaped after the live #888 generation, but importing the fixture project so
    the pre-flight guard passes and the criterion check is what decides.
    """
    return (
        "# AC#3: ... net 10.00, vat 1.75, total 11.76.\n"
        "# 11.76 is a typo in the spec; the implementation follows AC2 -> 11.75.\n"
        "from app.auth import login_user\n"
        "\n"
        "def test_line_total_total_is_11_75():\n"
        '    """AC#3 (corrected per AC2): total is 11.75."""\n'
        "    assert login_user is not None\n"
        "    assert 10.00 + 1.75 == 11.75\n"
        "    assert 0.175 and 200 and 1\n"
    )


def _faithful_criterion_source() -> str:
    """The same test asserting the criterion AS WRITTEN — it may fail; fine."""
    return (
        "# AC#3: ... net 10.00, vat 1.75, total 11.76.\n"
        "# NOTE: this contradicts AC2 (10.00 + 1.75 = 11.75). Asserting AC3 as\n"
        "# signed so the contradiction reaches a human.\n"
        "from app.auth import login_user\n"
        "\n"
        "def test_line_total_total_is_11_76():\n"
        "    assert login_user is not None\n"
        "    assert (10.00, 1.75, 0.175, 200, 1) and 11.76\n"
    )


@pytest.mark.asyncio
async def test_criterion_literal_drift_is_rejected(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """#888: a test that never asserts the criterion's value is not a verifier."""
    _make_plan(spec_dir, subtask_count=1)
    _set_description(spec_dir, _AC3_DESCRIPTION)
    mock_sdk(source_for=lambda sid: _rewritten_criterion_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "replan_needed"
    assert status["phase"] == "gen_functional_criterion_literal_rejected"
    assert "11.76" in status["last_rejected_reason"]
    # The rewritten file does not survive, and a human-reachable replan is filed.
    assert not (spec_dir / "tests" / "test_s0.py").exists()
    rr = json.loads((spec_dir / "context" / "replan_request.json").read_text())
    assert "11.76" in rr["reason"]


@pytest.mark.asyncio
async def test_criterion_asserted_as_written_commits(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """The mirror half: assert the criterion as signed and generation is clean."""
    _make_plan(spec_dir, subtask_count=1)
    _set_description(spec_dir, _AC3_DESCRIPTION)
    mock_sdk(source_for=lambda sid: _faithful_criterion_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"
    assert status["tests_generated"] == 1
    assert (spec_dir / "tests" / "test_s0.py").exists()
    assert not (spec_dir / "context" / "replan_request.json").exists()


@pytest.mark.asyncio
async def test_criterion_authority_rejects_the_specimen(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#995: prose that grades the spec is rejected on its own, values aside.

    The literal check is opted out here precisely so the values cannot be what
    rejects. What is left is the comment — "11.76 is a typo in the spec; the
    implementation follows AC2" — a generator announcing it decided the
    specification was wrong. That test has redefined its own oracle, and the
    literal check cannot see it whenever the asserted value happens to agree.
    """
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "0")
    _make_plan(spec_dir, subtask_count=1)
    _set_description(spec_dir, _AC3_DESCRIPTION)
    mock_sdk(source_for=lambda sid: _rewritten_criterion_source())

    assert await run_gen_functional(spec_dir, project_dir) is False

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "replan_needed"
    assert status["phase"] == "gen_functional_criterion_authority_rejected"
    assert "spec" in status["last_rejected_reason"]
    # The file does not survive and a human-reachable replan is filed.
    assert not (spec_dir / "tests" / "test_s0.py").exists()
    assert (spec_dir / "context" / "replan_request.json").exists()


@pytest.mark.asyncio
async def test_criterion_authority_check_is_what_rejects(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation check on the wiring: opt BOTH checks out and the same source
    sails through, so the rejection above is the authority check doing the work."""
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "0")
    monkeypatch.setenv("TFACTORY_CRITERION_AUTHORITY_CHECK", "0")
    _make_plan(spec_dir, subtask_count=1)
    _set_description(spec_dir, _AC3_DESCRIPTION)
    mock_sdk(source_for=lambda sid: _rewritten_criterion_source())

    assert await run_gen_functional(spec_dir, project_dir) is True
    assert json.loads((spec_dir / "status.json").read_text())["status"] == "generated"


@pytest.mark.asyncio
async def test_faithful_source_passes_the_authority_check(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """The mirror half: an honest test's prose is not a claim, and it commits."""
    _make_plan(spec_dir, subtask_count=1)
    _set_description(spec_dir, _AC3_DESCRIPTION)
    mock_sdk(source_for=lambda sid: _faithful_criterion_source())

    assert await run_gen_functional(spec_dir, project_dir) is True
    assert (spec_dir / "tests" / "test_s0.py").exists()


@pytest.mark.asyncio
async def test_criterion_literal_check_is_what_rejects(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation check on the wiring: with the check opted out, the SAME rewritten
    source sails through. So the rejection above is this check doing the work,
    not some other guard incidentally tripping.

    The #995 authority check is opted out too, because it independently rejects
    this same source — its comment reads "11.76 is a typo in the spec", which is
    the specimen #995 exists for. Leaving it on would make this test pass or
    fail for the other guard's reasons, which is the very confusion it is here
    to rule out. `test_criterion_authority_rejects_the_specimen` covers that
    direction.
    """
    monkeypatch.setenv("TFACTORY_CRITERION_LITERAL_CHECK", "0")
    monkeypatch.setenv("TFACTORY_CRITERION_AUTHORITY_CHECK", "0")
    _make_plan(spec_dir, subtask_count=1)
    _set_description(spec_dir, _AC3_DESCRIPTION)
    mock_sdk(source_for=lambda sid: _rewritten_criterion_source())

    assert await run_gen_functional(spec_dir, project_dir) is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"


# ── Session error path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_error_continues_to_next_subtask(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """A session error on s0 doesn't block s1 from succeeding."""
    _make_plan(spec_dir, subtask_count=2)
    mock_sdk(
        source_for=lambda sid: _valid_test_source() if sid == "s1" else None,
        status_for=lambda sid: "error" if sid == "s0" else "complete",
    )

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True  # one succeeded, that's enough for "generated"

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"
    assert status["tests_generated"] == 1


# ── Empty + missing-plan paths ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_pending_subtasks_is_generated_empty(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """Plan exists but all subtasks are already completed → warning, not failure."""
    _make_plan(spec_dir, subtask_count=1)
    plan = json.loads((spec_dir / "test_plan.json").read_text())
    plan["phases"][0]["subtasks"][0]["status"] = "completed"
    (spec_dir / "test_plan.json").write_text(json.dumps(plan))
    mock_sdk(source_for=lambda sid: _valid_test_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated_empty"
    assert status["tests_generated"] == 0
    # #1253: the legitimate zero must state its reason in a field the
    # completion envelope can read, not only in prose. Without this the
    # envelope cannot tell "nothing to generate" from "generation silently
    # produced nothing", and defaults the pair to the safe answer: refusal.
    assert status["verify_skip_reason"] == "no pending subtasks to generate"


@pytest.mark.asyncio
async def test_missing_plan_is_hard_failure(
    spec_dir: Path,
    project_dir: Path,
) -> None:
    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "gen_functional_failed"
    assert "no_plan" in status["phase"]


@pytest.mark.asyncio
async def test_missing_spec_dir_returns_false(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    ghost = tmp_path / "ghost"
    ok = await run_gen_functional(ghost, project_dir)
    assert ok is False


# ── schedule_gen_functional unchanged surface ──────────────────────────


def test_schedule_disabled_returns_none(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")

    async def _run():
        return schedule_gen_functional(spec_dir, project_dir)

    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_sdk,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "1")
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: _valid_test_source())

    task = schedule_gen_functional(spec_dir, project_dir)
    assert task is not None
    await task
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"


# ── Full chain: planner → gen_functional → planner replan ──────────────


@pytest.mark.asyncio
async def test_full_chain_rejection_loops_back_to_planner_replan(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When gen_functional rejects a subtask, the planner replan task is
    auto-scheduled. This test verifies the loop-back wiring is in place."""
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "1")
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")
    _make_plan(spec_dir, subtask_count=1)

    # Mock gen_functional's SDK to emit a hallucinated test that preflight rejects.
    class _FakeAsyncCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _gf_resolve(*a, **kw):
        return _FakeAsyncCM()

    async def _gf_invoke(client, prompt, spec_dir_arg, verbose):
        # Find the Write path
        write_path = None
        for line in prompt.splitlines():
            if line.startswith("- write the file at:"):
                write_path = line.split("`")[1]
                break
        if write_path:
            Path(write_path).parent.mkdir(parents=True, exist_ok=True)
            Path(write_path).write_text(_hallucinated_import_source())
        return "complete", "mock", {}

    monkeypatch.setattr("agents.gen_functional._resolve_client", _gf_resolve)
    monkeypatch.setattr("agents.gen_functional._invoke_session", _gf_invoke)

    # Mock the planner's SDK seams (called via the chain).
    planner_was_invoked = {"mode": None}

    async def _pl_resolve(*a, **kw):
        return _FakeAsyncCM()

    async def _pl_invoke(client, prompt, spec_dir_arg, verbose):
        # Record which mode the planner was invoked in.
        if "REPLAN CONTEXT" in prompt:
            planner_was_invoked["mode"] = "replan"
        else:
            planner_was_invoked["mode"] = "initial"
        # Emit a no-op plan-with-replan-phase to avoid further chaining.
        current = json.loads((spec_dir_arg / "test_plan.json").read_text())
        current["phases"].append(
            {
                "phase": 2,
                "name": "replan-1",
                "type": "implementation",
                "subtasks": [],
                "parallel_safe": False,
            }
        )
        (spec_dir_arg / "test_plan.json").write_text(json.dumps(current))
        return "complete", "mock", {}

    monkeypatch.setattr("agents.planner._resolve_planner_client", _pl_resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _pl_invoke)

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False

    # Drain the planner-replan task that gen_functional auto-scheduled.
    from agents.planner import _BG_PLANNER_TASKS

    if _BG_PLANNER_TASKS:
        await asyncio.gather(*list(_BG_PLANNER_TASKS), return_exceptions=True)

    # The chain reached planner replan mode.
    assert planner_was_invoked["mode"] == "replan"
    # The replan_request that gen_functional wrote is what the planner consumed.
    rr_path = spec_dir / "context" / "replan_request.json"
    assert rr_path.exists()


# ── v0.2: _resolve_framework_descriptor unit tests (Task 6 / #22) ──────


def test_resolve_framework_descriptor_returns_none_for_no_framework() -> None:
    """v0.1-style subtask (framework=None) → descriptor is None."""
    subtask = {"id": "x", "description": "y"}
    result = _resolve_framework_descriptor(subtask)
    assert result is None


def test_resolve_framework_descriptor_returns_none_for_dataclass_no_framework() -> None:
    """v0.1-style Subtask dataclass (no framework field set) → None."""
    from test_plan import Lane
    from test_plan import Subtask as SubtaskDC

    st = SubtaskDC(id="t1", description="d", lane=Lane.UNIT)
    assert st.framework is None
    result = _resolve_framework_descriptor(st)
    assert result is None


def test_resolve_framework_descriptor_returns_descriptor_for_pytest() -> None:
    """v0.2: subtask.framework='pytest' → FrameworkDescriptor with name='pytest'."""
    subtask = {"id": "x", "description": "y", "framework": "pytest"}
    result = _resolve_framework_descriptor(subtask)
    assert result is not None
    assert result.name == "pytest"
    assert result.runtime.image == "tfactory-runner-pytest:latest"


def test_resolve_framework_descriptor_returns_descriptor_for_jest() -> None:
    """v0.2: subtask.framework='jest' → FrameworkDescriptor with name='jest'."""
    subtask = {"id": "x", "description": "y", "framework": "jest"}
    result = _resolve_framework_descriptor(subtask)
    assert result is not None
    assert result.name == "jest"
    assert result.runtime.image == "tfactory-runner-jest:latest"


def test_resolve_framework_descriptor_returns_descriptor_for_playwright() -> None:
    """v0.2: subtask.framework='playwright' → FrameworkDescriptor with name='playwright'."""
    subtask = {"id": "x", "description": "y", "framework": "playwright"}
    result = _resolve_framework_descriptor(subtask)
    assert result is not None
    assert result.name == "playwright"
    assert result.runtime.image == "tfactory-runner-playwright:latest"


def test_resolve_framework_descriptor_raises_for_unknown_framework() -> None:
    """v0.2: unknown framework → LookupError with helpful message."""
    subtask = {"id": "x", "description": "y", "framework": "my-fake-framework-xyz"}
    with pytest.raises(LookupError, match="my-fake-framework-xyz"):
        _resolve_framework_descriptor(subtask)


def test_resolve_framework_descriptor_lookup_error_mentions_available_frameworks() -> (
    None
):
    """LookupError message lists available frameworks for diagnosis."""
    subtask = {"id": "x", "description": "y", "framework": "no-such-one"}
    with pytest.raises(LookupError) as exc_info:
        _resolve_framework_descriptor(subtask)
    msg = str(exc_info.value)
    # Should mention at least one known framework name
    assert any(fw in msg for fw in ["pytest", "jest", "playwright"])


# ── v0.2: _resolve_runner_fn unit tests (Task 6 / #22) ─────────────────


def test_runner_fn_parameterized_by_descriptor_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_runner_fn reads image from descriptor.runtime.image."""
    captured: dict = {}

    class FakeRuntime:
        image = "tfactory-runner-jest:latest"

    class FakeDesc:
        runtime = FakeRuntime()

    class FakeDockerRunner:
        def __init__(self, image=None, **kwargs):
            captured["image"] = image

        def run_pytest(self, **kwargs):
            return None

    import agents.gen_functional as gf_mod

    monkeypatch.setattr(
        "agents.gen_functional.DockerRunner",
        None,
        raising=False,
    )
    # We need to inject into the lazy import path inside _resolve_runner_fn.
    # Monkeypatch the module that will be imported at call time.
    import tools.runners.docker_runner as dr_mod

    original = dr_mod.DockerRunner
    dr_mod.DockerRunner = FakeDockerRunner
    try:
        _resolve_runner_fn(framework_descriptor=FakeDesc())
        assert captured["image"] == "tfactory-runner-jest:latest"
    finally:
        dr_mod.DockerRunner = original


def test_runner_fn_legacy_uses_default_image_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_runner_fn(None) → tfactory-runner-python:latest + DeprecationWarning."""
    import warnings

    captured: dict = {}

    class FakeDockerRunner:
        def __init__(self, image=None, **kwargs):
            captured["image"] = image

        def run_pytest(self, **kwargs):
            return None

    import tools.runners.docker_runner as dr_mod

    original = dr_mod.DockerRunner
    dr_mod.DockerRunner = FakeDockerRunner
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _resolve_runner_fn(framework_descriptor=None)
        depr = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert depr, "expected DeprecationWarning for legacy path"
        assert captured["image"] == "tfactory-runner-python:latest"
    finally:
        dr_mod.DockerRunner = original


# ── v0.2: run_gen_functional dispatches framework subtasks ───────────────


def _make_plan_with_framework(spec_dir: Path, framework: str, lang: str) -> None:
    """Write a test_plan.json with ONE pending Lane.UNIT subtask tagged with (lang, framework).

    Note: Gen-Functional currently dispatches on Lane.UNIT regardless of framework;
    the framework field controls which descriptor (and thus which prompt + runner image)
    is used. Browser-lane routing is a v0.3 concern (Task 10).
    """
    ext = "py" if framework == "pytest" else "spec.ts"
    plan = {
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "AC#1",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "s0",
                        "description": f"test with {framework}",
                        "status": "pending",
                        "lane": "unit",  # always unit so gen_functional picks it up
                        "language": lang,
                        "framework": framework,
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1",
                        "files_to_create": [f"tests/test_s0.{ext}"],
                        "verification": {
                            "type": "command",
                            "run": f"pytest tests/test_s0.{ext}"
                            if framework == "pytest"
                            else f"npx {framework} tests/test_s0.{ext}",
                        },
                    }
                ],
                "parallel_safe": False,
            }
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    }
    (spec_dir / "test_plan.json").write_text(json.dumps(plan))


def _mock_sdk_capture_prompt(monkeypatch: pytest.MonkeyPatch, captured_prompts: list):
    """Set up SDK mocks that capture the assembled prompt text.

    The mock writes the valid test source at the path declared in the prompt
    so the agent thinks the SDK agent completed successfully.
    """

    class _FakeAsyncCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _resolve(*a, **kw):
        return _FakeAsyncCM()

    async def _invoke(client, prompt, spec_dir_arg, verbose):
        captured_prompts.append(prompt)
        for line in prompt.splitlines():
            if line.startswith("- write the file at:"):
                write_path = Path(line.split("`")[1])
                write_path.parent.mkdir(parents=True, exist_ok=True)
                write_path.write_text(_valid_test_source())
                break
        return "complete", "mock", {}

    monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
    monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)


@pytest.mark.asyncio
async def test_gen_functional_dispatches_pytest_subtask(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pytest subtask: descriptor resolved, FRAMEWORK CONTEXT (pytest) in prompt."""
    _make_plan_with_framework(spec_dir, "pytest", "python")
    captured_prompts: list[str] = []
    _mock_sdk_capture_prompt(monkeypatch, captured_prompts)

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True
    assert captured_prompts, "no prompt was assembled"
    assert "## FRAMEWORK CONTEXT (pytest)" in captured_prompts[0]


@pytest.mark.asyncio
async def test_gen_functional_dispatches_jest_subtask(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jest subtask: FRAMEWORK CONTEXT (jest) present in prompt sent to SDK."""
    _make_plan_with_framework(spec_dir, "jest", "typescript")
    captured_prompts: list[str] = []
    _mock_sdk_capture_prompt(monkeypatch, captured_prompts)

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True
    assert captured_prompts, "no prompt assembled for jest subtask"
    assert "## FRAMEWORK CONTEXT (jest)" in captured_prompts[0]


@pytest.mark.asyncio
async def test_gen_functional_dispatches_playwright_subtask(
    spec_dir: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """playwright subtask: FRAMEWORK CONTEXT (playwright) present in prompt."""
    _make_plan_with_framework(spec_dir, "playwright", "typescript")
    captured_prompts: list[str] = []
    _mock_sdk_capture_prompt(monkeypatch, captured_prompts)

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True
    assert captured_prompts
    assert "## FRAMEWORK CONTEXT (playwright)" in captured_prompts[0]


@pytest.mark.asyncio
async def test_gen_functional_legacy_subtask_uses_default_image_with_warning(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """v0.1 subtask (no framework): DeprecationWarning raised via prompt helper."""
    _make_plan(spec_dir, subtask_count=1)
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mock_sdk(source_for=lambda sid: _valid_test_source())
        ok = await run_gen_functional(spec_dir, project_dir)

    assert ok is True
    depr = [x for x in w if issubclass(x.category, DeprecationWarning)]
    assert depr, "expected DeprecationWarning for v0.1-style subtask"


@pytest.mark.asyncio
async def test_gen_functional_unknown_framework_fails_gracefully(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """Unknown framework in subtask → LookupError raised, status=gen_functional_failed."""
    _make_plan_with_framework(spec_dir, "my-fake-xyz", "typescript")
    mock_sdk(source_for=lambda sid: _valid_test_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    # The LookupError from _resolve_framework_descriptor should cause a failure
    assert status["status"] == "gen_functional_failed"


@pytest.mark.asyncio
async def test_gen_functional_writes_v01_legacy_path_when_descriptor_none(
    spec_dir: Path,
    project_dir: Path,
) -> None:
    """v0.1 subtask (no framework): SDK is called with the legacy prompt content."""
    _make_plan(spec_dir, subtask_count=1)
    captured_prompts: list[str] = []
    import warnings

    class _FakeAsyncCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _resolve(*a, **kw):
        return _FakeAsyncCM()

    async def _invoke(client, prompt, spec_dir_arg, verbose):
        captured_prompts.append(prompt)
        for line in prompt.splitlines():
            if line.startswith("- write the file at:"):
                write_path = Path(line.split("`")[1])
                write_path.parent.mkdir(parents=True, exist_ok=True)
                write_path.write_text(_valid_test_source())
                break
        return "complete", "mock", {}

    import pytest as pt

    pt.MonkeyPatch().setattr("agents.gen_functional._resolve_client", _resolve)
    pt.MonkeyPatch().setattr("agents.gen_functional._invoke_session", _invoke)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ok = await run_gen_functional(spec_dir, project_dir)

    assert ok is True
    assert captured_prompts
    # The legacy prompt body includes Python-specific guidance
    assert "pytest" in captured_prompts[0]
    # A DeprecationWarning was issued
    depr = [x for x in w if issubclass(x.category, DeprecationWarning)]
    assert depr


# ── #707: partial-verify + replan-reason persistence ────────────────────


@pytest.mark.asyncio
async def test_partial_plan_verifies_committed_despite_stuck(
    spec_dir: Path,
    project_dir: Path,
) -> None:
    """#707 (B): 1 committed test + 1 stuck subtask, none pending → the spec
    still advances to verify the committed test instead of generated_empty."""
    committed_rel = "tests/test_committed.py"
    plan = {
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "AC#1",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "done",
                        "description": "already generated",
                        "status": "completed",
                        "lane": "unit",
                        "files_to_create": [committed_rel],
                    },
                    {
                        "id": "stuck",
                        "description": "gave up",
                        "status": "stuck",
                        "lane": "api",
                        "replan_count": 2,
                        "files_to_create": ["tests/test_stuck.py"],
                    },
                ],
                "parallel_safe": False,
            }
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    }
    (spec_dir / "test_plan.json").write_text(json.dumps(plan))
    # The committed subtask's file must actually exist on disk to count.
    (spec_dir / committed_rel).write_text(_valid_test_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated"
    assert status["phase"] == "gen_functional_partial_verify"
    assert status["tests_generated"] == 1


@pytest.mark.asyncio
async def test_no_committed_and_no_pending_stays_generated_empty(
    spec_dir: Path,
    project_dir: Path,
) -> None:
    """#707 (B) negative: a completed subtask whose file was NOT committed
    to disk does not count — still generated_empty (no false verify)."""
    plan = {
        "feature": "demo",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "AC#1",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "done",
                        "description": "claims done, no file",
                        "status": "completed",
                        "lane": "unit",
                        "files_to_create": ["tests/test_missing.py"],
                    }
                ],
                "parallel_safe": False,
            }
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    }
    (spec_dir / "test_plan.json").write_text(json.dumps(plan))

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "generated_empty"


@pytest.mark.asyncio
async def test_rejection_persists_replan_reason(
    spec_dir: Path,
    project_dir: Path,
    mock_sdk,
) -> None:
    """#707 (A): a guardrail rejection records WHY into status.json
    (replan_reasons list) and onto the subtask (test_plan.json)."""
    _make_plan(spec_dir, subtask_count=1)
    mock_sdk(source_for=lambda sid: _hallucinated_import_source())

    ok = await run_gen_functional(spec_dir, project_dir)
    assert ok is False

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["last_rejected_reason"]
    assert "pre-flight rejected" in status["last_rejected_reason"]
    reasons = status["replan_reasons"]
    assert len(reasons) == 1
    assert reasons[0]["subtask_id"] == "s0"
    assert "pre-flight rejected" in reasons[0]["reason"]

    # The reason also rides along on the subtask record in test_plan.json.
    plan = json.loads((spec_dir / "test_plan.json").read_text())
    st = plan["phases"][0]["subtasks"][0]
    assert "pre-flight rejected" in st["replan_reason"]
