"""Every terminal verify status reaches CFactory exactly once (#1366).

``notify_terminal`` lazily reads ``agents.triager._notify_completion`` at call
time, so one recorder patched there observes every writer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agents.workspace_status import (
    TERMINAL_STATUSES,
    anchor_stage_task,
    notify_terminal,
    write_status_patch,
)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(
        "agents.triager._notify_completion",
        lambda _spec_dir, status: seen.append(status["status"]),
    )
    return seen


def _spec(tmp_path: Path, status: str) -> Path:
    spec_dir = tmp_path / "workspaces" / "p1" / "specs" / "s1"
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text(json.dumps({"status": status}))
    return spec_dir


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_terminal_write_notifies_once(
    tmp_path: Path, calls: list[str], status: str
) -> None:
    write_status_patch(_spec(tmp_path, "generating"), "x", status=status)
    assert calls == [status]


@pytest.mark.parametrize("status", ["evaluator_failed", "generating"])
def test_non_terminal_write_does_not_notify(
    tmp_path: Path, calls: list[str], status: str
) -> None:
    write_status_patch(_spec(tmp_path, "planning"), "x", status=status)
    assert calls == []


@pytest.mark.asyncio
async def test_anchor_crash_notifies_evaluator_failed(
    tmp_path: Path, calls: list[str]
) -> None:
    spec_dir = _spec(tmp_path, "generated")

    async def boom() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(boom())
    anchor_stage_task(
        task,
        set(),
        spec_dir=spec_dir,
        stage="evaluator",
        failed_status="evaluator_failed",
    )
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert calls == ["evaluator_failed"]


@pytest.mark.asyncio
async def test_schedule_evaluator_notifies_on_failure(
    tmp_path: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents.evaluator as ev

    spec_dir = _spec(tmp_path, "generated")

    async def fake_run(sd: Path, pd: Path, mode: str = "initial") -> bool:
        write_status_patch(sd, "evaluator", status="evaluator_failed")
        return False

    monkeypatch.setattr(ev, "run_evaluator", fake_run)
    monkeypatch.delenv("TFACTORY_AUTO_EVALUATE", raising=False)
    task = ev.schedule_evaluator(spec_dir, tmp_path)
    assert task is not None
    await task
    assert calls == ["evaluator_failed"]


def test_reaper_notifies_failed(tmp_path: Path, calls: list[str]) -> None:
    from agents.verify_dispatch import _mark_spec_reaped

    spec_dir = _spec(tmp_path, "evaluating")
    _mark_spec_reaped({"worker_ref": {"spec_dir": str(spec_dir)}}, "gone")
    assert calls == ["failed"]


def test_orphan_reconcile_notifies_failed(tmp_path: Path, calls: list[str]) -> None:
    from agents.liveness_sweep import reconcile_inline_orphans

    _spec(tmp_path, "planning")
    reconcile_inline_orphans(tmp_path)
    assert calls == ["failed"]


def test_notify_terminal_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raiser(*_a: object) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr("agents.triager._notify_completion", raiser)
    notify_terminal(_spec(tmp_path, "failed"))
