"""Tests for the Triager auto-fire scaffold + stub — Task 8 (#9) commit 1.

Mirrors ``tests/test_evaluator.py``'s commit-1 stub-era shape. Real
agent (commits 2-5) replaces the stub body; this file locks down the
*scaffold* (scheduler, env gate, GC anchor, forward chain) so the
rest of Task 8 builds on a green base.

Covered:
  - Stub run_triager status transitions: evaluated → triaging →
    triaged_empty, with placeholder triage_report.{md,json} emitted
  - Mode parameter reflected in phase + reports
  - findings/ auto-created if missing
  - Hard-failure path → triager_failed with error captured
  - schedule_triager respects TFACTORY_AUTO_TRIAGE env gate
  - GC anchor: scheduled task is held until done
  - Forward chain from evaluator's success path fires
    schedule_triager (gated by env)
  - _advance_to_triager swallows ImportError (defensive)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.triager import (
    _BG_TRIAGER_TASKS,
    run_triager,
    schedule_triager,
)


# ── autouse: keep all chains deterministic ─────────────────────────────


@pytest.fixture(autouse=True)
def _disable_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    """Workspace mimicking what the Evaluator just left behind."""
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-feat"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(json.dumps({
        "task_id": "001-feat",
        "project_id": "demo",
        "spec_id": "001-feat",
        "status": "evaluated",
        "phase": "evaluator_complete",
        "verdicts_count": 3,
        "tests_evaluated": 3,
    }))
    # Seed an empty verdicts.json so the future real Triager has
    # something to read (stub ignores it, but the directory shape is
    # what downstream consumers will see).
    (d / "findings" / "verdicts.json").write_text(json.dumps({
        "evaluator_version": "task7-commit5",
        "verdicts": [],
    }))
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


# ── Stub status-transition tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_advances_to_triaged_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    ok = await run_triager(spec_dir, project_dir, mode="initial")
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triaged_empty"
    assert status["phase"] == "triager_stub_no_op"
    assert status["committed_count"] == 0
    assert status["rejected_count"] == 0
    assert status["flagged_count"] == 0
    assert "updated_at" in status


@pytest.mark.asyncio
async def test_stub_emits_report_placeholders(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Two placeholder files land — Task 9's portal reads these."""
    await run_triager(spec_dir, project_dir, mode="initial")

    report_json = spec_dir / "findings" / "triage_report.json"
    report_md = spec_dir / "findings" / "triage_report.md"
    assert report_json.exists()
    assert report_md.exists()

    j = json.loads(report_json.read_text())
    assert j["triager_version"] == "stub-task8-commit1"
    assert j["mode"] == "initial"
    assert j["committed"] == []
    assert j["rejected"] == []
    assert j["flagged"] == []
    assert "generated_at" in j

    assert "# Triage Report (stub)" in report_md.read_text()


@pytest.mark.asyncio
async def test_stub_preserves_upstream_status_fields(
    spec_dir: Path, project_dir: Path,
) -> None:
    """The verdicts_count / tests_evaluated from the Evaluator's
    upstream patch should survive the Triager stub's writes (we just
    add fields, not clobber)."""
    await run_triager(spec_dir, project_dir, mode="initial")
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["verdicts_count"] == 3
    assert status["tests_evaluated"] == 3
    assert status["spec_id"] == "001-feat"


@pytest.mark.asyncio
async def test_stub_creates_findings_dir_if_missing(
    spec_dir: Path, project_dir: Path,
) -> None:
    import shutil
    shutil.rmtree(spec_dir / "findings")
    ok = await run_triager(spec_dir, project_dir)
    assert ok is True
    assert (spec_dir / "findings" / "triage_report.json").exists()


@pytest.mark.asyncio
async def test_stub_mode_rerun_reflected(
    spec_dir: Path, project_dir: Path,
) -> None:
    await run_triager(spec_dir, project_dir, mode="rerun")
    report = json.loads(
        (spec_dir / "findings" / "triage_report.json").read_text()
    )
    assert report["mode"] == "rerun"


@pytest.mark.asyncio
async def test_stub_handles_hard_failure(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force a status write to raise; status lands at triager_failed."""
    from agents import triager

    real_write = triager._write_status_patch
    call_count = {"n": 0}

    def _bomb(sd, **fields):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk full")
        return real_write(sd, **fields)

    monkeypatch.setattr(triager, "_write_status_patch", _bomb)
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triager_failed"
    assert "disk full" in status.get("triager_error", "")


# ── schedule_triager: env gating + GC anchor ──────────────────────────


def test_schedule_disabled_returns_none(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")
    async def _run():
        return schedule_triager(spec_dir, project_dir)
    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "1")
    task = schedule_triager(spec_dir, project_dir)
    assert task is not None
    assert task in _BG_TRIAGER_TASKS
    await task
    assert task not in _BG_TRIAGER_TASKS


@pytest.mark.asyncio
async def test_schedule_default_is_on(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TFACTORY_AUTO_TRIAGE", raising=False)
    task = schedule_triager(spec_dir, project_dir)
    assert task is not None
    await task


# ── Forward chain from evaluator ────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluator_success_path_schedules_triager(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When evaluator writes status=evaluated, its forward chain helper
    calls schedule_triager. Opt in to the chain here."""
    from agents import evaluator

    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "1")
    captured: dict = {}

    def _capture_schedule(sd, pd, mode="initial"):
        captured["spec_dir"] = sd
        captured["project_dir"] = pd
        captured["mode"] = mode
        return None

    import agents.triager as tri_mod
    monkeypatch.setattr(tri_mod, "schedule_triager", _capture_schedule)

    evaluator._advance_to_triager(spec_dir, project_dir)
    assert captured["spec_dir"] == spec_dir
    assert captured["project_dir"] == project_dir
    assert captured["mode"] == "initial"


def test_advance_to_triager_swallows_import_errors(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_advance_to_triager must not crash if agents.triager can't be
    imported (defensive, mirrors planner / gen_functional / evaluator)."""
    from agents import evaluator

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def _selective_raiser(name, *args, **kwargs):
        if name == "agents.triager":
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_selective_raiser):
        evaluator._advance_to_triager(spec_dir, project_dir)
