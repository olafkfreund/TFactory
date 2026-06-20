#!/usr/bin/env python3
"""Tests for the in-Job verify orchestration entrypoint (RFC-0016, TFactory #466).

``agents.verify_pipeline`` is the deterministic evaluate→triage runnable the
k8s Job executes. These tests mock ``run_evaluator`` / ``run_triager`` (so no
LLM / docker / cluster) and verify:

- It runs the evaluator THEN the triager, and disables fire-and-forget
  auto-advance (a one-shot Job must run inline, not leave triage detached).
- ``run_verify_pipeline`` returns ``(ok, final_status)`` read from status.json.
- The terminal job-state write classifies a real verdict as a verdict, a
  ``*_failed`` status as a failure, and a no-verdict terminal status as #464.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents import verify_pipeline as vp


def _write_status(spec_dir: Path, status: str) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(json.dumps({"status": status}))


async def test_runs_evaluator_then_triager_inline(monkeypatch, tmp_path):
    calls: list[str] = []

    async def fake_eval(spec_dir, project_dir, mode="initial"):
        calls.append("eval")
        _write_status(spec_dir, "evaluated")
        return True

    async def fake_triage(spec_dir, project_dir, mode="initial"):
        calls.append("triage")
        _write_status(spec_dir, "triaged")
        return True

    monkeypatch.setattr("agents.evaluator.run_evaluator", fake_eval)
    monkeypatch.setattr("agents.triager.run_triager", fake_triage)
    # Pretend auto-advance was ON; the pipeline must turn it OFF for its run.
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "1")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "1")

    ok, final_status = await vp.run_verify_pipeline(tmp_path, tmp_path)

    assert calls == ["eval", "triage"]  # evaluator first, then triager, inline
    assert ok is True
    assert final_status == "triaged"
    # The Job owns the chain — auto-advance disabled so no detached triager.
    import os

    assert os.environ["TFACTORY_AUTO_TRIAGE"] == "0"
    assert os.environ["TFACTORY_AUTO_EVALUATE"] == "0"


async def test_pipeline_reports_failure_on_evaluator_miss(monkeypatch, tmp_path):
    async def fake_eval(spec_dir, project_dir, mode="initial"):
        _write_status(spec_dir, "evaluator_failed")
        return False

    async def fake_triage(spec_dir, project_dir, mode="initial"):
        # Triager still runs (renders the honest report) but keeps the failure.
        return False

    monkeypatch.setattr("agents.evaluator.run_evaluator", fake_eval)
    monkeypatch.setattr("agents.triager.run_triager", fake_triage)

    ok, final_status = await vp.run_verify_pipeline(tmp_path, tmp_path)
    assert ok is False
    assert final_status == "evaluator_failed"


# ─── terminal classification (#464 no-verdict→stuck) ──────────────────────────


class _RecordingStore:
    """Captures the kwargs the pipeline passes to record_terminal."""

    def __init__(self):
        self.calls: list[dict] = []

    async def record_terminal(self, job_id, **kw):
        self.calls.append({"job_id": job_id, **kw})


async def _record_with_store(monkeypatch, *, job_id, final_status):
    store = _RecordingStore()
    import sys
    import types

    # Stub the sibling-app module the lazy import reaches, so the backend test
    # venv (which doesn't carry apps/web-server on the path by default) resolves
    # ``server.services.job_state_store`` to our recorder.
    server = types.ModuleType("server")
    services = types.ModuleType("server.services")
    jss = types.ModuleType("server.services.job_state_store")
    jss.record_terminal = store.record_terminal  # type: ignore[attr-defined]
    services.job_state_store = jss  # type: ignore[attr-defined]
    server.services = services  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setitem(sys.modules, "server.services", services)
    monkeypatch.setitem(sys.modules, "server.services.job_state_store", jss)

    await vp._record_terminal(job_id, final_status=final_status)
    return store


async def test_terminal_real_verdict_has_verdict_true(monkeypatch):
    store = await _record_with_store(monkeypatch, job_id="j1", final_status="triaged")
    assert store.calls[0]["has_verdict"] is True
    assert store.calls[0]["error"] is None
    assert store.calls[0]["service_status"] == "triaged"


async def test_terminal_failed_status_carries_error(monkeypatch):
    store = await _record_with_store(
        monkeypatch, job_id="j2", final_status="triager_failed"
    )
    assert store.calls[0]["has_verdict"] is False
    assert store.calls[0]["error"]  # never-overclaim


async def test_terminal_no_verdict_status_marks_no_verdict(monkeypatch):
    # A terminal-by-name status with no real verdict → has_verdict False so the
    # store maps it to `stuck` (#464 lanes-pending-no-verdict).
    store = await _record_with_store(monkeypatch, job_id="j3", final_status="reviewing")
    assert store.calls[0]["has_verdict"] is False


async def test_terminal_write_is_skipped_when_store_unavailable(monkeypatch, caplog):
    # No `server` module importable → best-effort skip, never raises.
    import sys

    for mod in ("server", "server.services", "server.services.job_state_store"):
        monkeypatch.setitem(sys.modules, mod, None)
    # Should not raise even though the import fails.
    await vp._record_terminal("j4", final_status="triaged")


def test_main_exits_nonzero_on_failure(monkeypatch, tmp_path):
    async def fake_pipeline(spec_dir, project_dir, *, mode="initial"):
        return False, "triager_failed"

    monkeypatch.setattr(vp, "run_verify_pipeline", fake_pipeline)
    rc = vp.main(["--spec", str(tmp_path), "--project", str(tmp_path)])
    assert rc == 1


def test_main_exits_zero_on_success(monkeypatch, tmp_path):
    async def fake_pipeline(spec_dir, project_dir, *, mode="initial"):
        return True, "triaged"

    recorded = {}

    async def fake_record(job_id, *, final_status):
        recorded["job_id"] = job_id
        recorded["final_status"] = final_status

    monkeypatch.setattr(vp, "run_verify_pipeline", fake_pipeline)
    monkeypatch.setattr(vp, "_record_terminal", fake_record)
    rc = vp.main(
        ["--spec", str(tmp_path), "--project", str(tmp_path), "--job-id", "jX"]
    )
    assert rc == 0
    assert recorded == {"job_id": "jX", "final_status": "triaged"}
