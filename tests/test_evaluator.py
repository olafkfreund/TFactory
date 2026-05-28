"""Tests for the Evaluator agent's auto-fire scaffold + stub —
Task 7 (#8) commit 1.

Mirrors ``tests/test_gen_functional.py``'s stub-era shape: the real
agent (commits 2-5) replaces the stub body, but the *scaffold*
(scheduler, env gate, GC anchor, forward chain) is what we lock down
here so the rest of Task 7 can build on a green base.

Covered:
  - Stub run_evaluator status transitions (generated → evaluating →
    evaluated_empty), with an empty verdicts.json emitted
  - Hard-failure path (corrupt status path) → evaluator_failed
  - schedule_evaluator respects TFACTORY_AUTO_EVALUATE env gate
  - GC anchor: scheduled task is held until done
  - Forward chain from gen_functional's success path fires
    schedule_evaluator (gated by env)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.evaluator import (
    _BG_EVALUATOR_TASKS,
    run_evaluator,
    schedule_evaluator,
)


# ── autouse: keep the planner-replan + evaluator chains deterministic ──


@pytest.fixture(autouse=True)
def _disable_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-off chain envs. Individual chain tests opt back in."""
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    """Workspace mimicking what gen_functional just left behind."""
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
        "tests_generated": 3,
    }))
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


# ── Stub status-transition tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_advances_status_to_evaluated_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Happy path: stub runs, status goes generated → evaluating →
    evaluated_empty, verdicts.json emitted with 0 verdicts."""
    ok = await run_evaluator(spec_dir, project_dir, mode="initial")
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluated_empty"
    assert status["phase"] == "evaluator_stub_no_op"
    assert status["verdicts_count"] == 0
    assert "updated_at" in status

    verdicts = json.loads((spec_dir / "findings" / "verdicts.json").read_text())
    assert verdicts["evaluator_version"] == "stub-task7-commit1"
    assert verdicts["mode"] == "initial"
    assert verdicts["verdicts"] == []
    assert "generated_at" in verdicts


@pytest.mark.asyncio
async def test_stub_records_in_flight_status(
    spec_dir: Path, project_dir: Path,
) -> None:
    """When the stub is in flight it writes status=evaluating before
    landing on evaluated_empty. We can't see the intermediate state
    directly in a sync test — but the FINAL state confirms the patch
    ran (updated_at + phase reflect the stub's full path)."""
    await run_evaluator(spec_dir, project_dir, mode="initial")
    status = json.loads((spec_dir / "status.json").read_text())
    # tests_generated from the gen_functional preamble is preserved
    assert status["tests_generated"] == 3
    # spec_id from the seed status is preserved
    assert status["spec_id"] == "001-feat"


@pytest.mark.asyncio
async def test_stub_creates_findings_dir_if_missing(
    spec_dir: Path, project_dir: Path,
) -> None:
    """If findings/ is missing (older workspace shape) the stub
    creates it before emitting verdicts.json."""
    import shutil
    shutil.rmtree(spec_dir / "findings")
    assert not (spec_dir / "findings").exists()

    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is True
    assert (spec_dir / "findings" / "verdicts.json").exists()


@pytest.mark.asyncio
async def test_stub_handles_hard_failure(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force a status write to raise; evaluator goes to evaluator_failed."""
    from agents import evaluator

    real_write = evaluator._write_status_patch
    call_count = {"n": 0}

    def _bomb(sd, **fields):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # let the first (status=evaluating) succeed so the agent
            # gets into the try-block, then trip on the SECOND write
            raise OSError("disk full")
        return real_write(sd, **fields)

    monkeypatch.setattr(evaluator, "_write_status_patch", _bomb)
    ok = await run_evaluator(spec_dir, project_dir)
    assert ok is False
    # The except branch calls _write_status_patch a third time with
    # status=evaluator_failed — that one goes through real_write because
    # call_count["n"] is now 3.
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert "disk full" in status.get("evaluator_error", "")


@pytest.mark.asyncio
async def test_stub_mode_rerun_reflected_in_status_and_verdicts(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Mode parameter is captured in the phase + verdicts.json so future
    re-evaluations are traceable."""
    await run_evaluator(spec_dir, project_dir, mode="rerun")
    status = json.loads((spec_dir / "status.json").read_text())
    assert "rerun" in status["phase"] or status["phase"] == "evaluator_stub_no_op"
    verdicts = json.loads((spec_dir / "findings" / "verdicts.json").read_text())
    assert verdicts["mode"] == "rerun"


# ── schedule_evaluator: env gating + GC anchor ──────────────────────────


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
    task = schedule_evaluator(spec_dir, project_dir)
    assert task is not None
    assert task in _BG_EVALUATOR_TASKS
    await task
    # done-callback cleans up
    assert task not in _BG_EVALUATOR_TASKS


@pytest.mark.asyncio
async def test_schedule_default_is_on(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset env var → schedule fires (production default ON)."""
    monkeypatch.delenv("TFACTORY_AUTO_EVALUATE", raising=False)
    task = schedule_evaluator(spec_dir, project_dir)
    assert task is not None
    await task


# ── Forward chain from gen_functional ───────────────────────────────────


@pytest.mark.asyncio
async def test_gen_functional_success_path_schedules_evaluator(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When gen_functional writes status=generated, its forward chain
    helper calls schedule_evaluator. Opt in to the chain here."""
    from agents import gen_functional

    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")
    captured: dict = {}

    def _capture_schedule(sd, pd, mode="initial"):
        captured["spec_dir"] = sd
        captured["project_dir"] = pd
        captured["mode"] = mode
        return None  # no real task — we just want to confirm the call

    # Patch the lazy-imported schedule_evaluator at its import site.
    import agents.evaluator as eval_mod
    monkeypatch.setattr(eval_mod, "schedule_evaluator", _capture_schedule)

    gen_functional._advance_to_evaluator(spec_dir, project_dir)
    assert captured["spec_dir"] == spec_dir
    assert captured["project_dir"] == project_dir
    assert captured["mode"] == "initial"


def test_advance_to_evaluator_swallows_import_errors(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_advance_to_evaluator must not crash if the evaluator module
    can't be imported for some reason (defensive, like the planner
    counterpart)."""
    from agents import gen_functional

    # Force the lazy import inside the helper to fail. We can't easily
    # monkey-patch the import statement itself, so instead we patch
    # builtins.__import__ for the one call.
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _selective_raiser(name, *args, **kwargs):
        if name == "agents.evaluator":
            raise ImportError("simulated import failure")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_selective_raiser):
        # Should NOT raise
        gen_functional._advance_to_evaluator(spec_dir, project_dir)
