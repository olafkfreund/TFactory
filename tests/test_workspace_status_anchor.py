"""Tests for ``anchor_stage_task`` — the #714 fire-and-forget failure capture.

The verify stages fire the next stage as a detached asyncio task. The old
done-callback discarded the anchor without inspecting ``task.exception()``, so a
background stage that crashed OUTSIDE its own try/except silently stranded the
spec (no verdict, no log — the #714 "stops after review" stall). ``anchor_stage_
task`` captures that crash as a terminal ``<failed_status>`` with the reason.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agents.workspace_status import anchor_stage_task, read_status


def _write_initial_status(spec_dir: Path, status: str) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(json.dumps({"status": status}))


@pytest.mark.asyncio
async def test_crash_is_captured_as_failed_status(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    _write_initial_status(spec_dir, "generated")
    anchor: set[asyncio.Task] = set()

    async def _boom() -> None:
        raise RuntimeError("evaluator exploded")

    task = anchor_stage_task(
        asyncio.create_task(_boom()),
        anchor,
        spec_dir=spec_dir,
        stage="evaluator",
        failed_status="evaluator_failed",
    )
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)  # let the done-callback run

    status = read_status(spec_dir)
    assert status["status"] == "evaluator_failed"
    assert "evaluator exploded" in status["evaluator_error"]
    assert task not in anchor  # anchor released


@pytest.mark.asyncio
async def test_clean_completion_leaves_status_untouched(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    _write_initial_status(spec_dir, "evaluated")
    anchor: set[asyncio.Task] = set()

    async def _ok() -> None:
        return None

    task = anchor_stage_task(
        asyncio.create_task(_ok()),
        anchor,
        spec_dir=spec_dir,
        stage="evaluator",
        failed_status="evaluator_failed",
    )
    await task
    await asyncio.sleep(0)

    assert read_status(spec_dir)["status"] == "evaluated"  # not clobbered
    assert task not in anchor


@pytest.mark.asyncio
async def test_cancellation_is_not_a_failure(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    _write_initial_status(spec_dir, "evaluating")
    anchor: set[asyncio.Task] = set()

    async def _sleep() -> None:
        await asyncio.sleep(10)

    task = anchor_stage_task(
        asyncio.create_task(_sleep()),
        anchor,
        spec_dir=spec_dir,
        stage="triager",
        failed_status="triager_failed",
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    # A cancelled task (loop shutdown) must NOT be recorded as a stage failure.
    assert read_status(spec_dir)["status"] == "evaluating"
    assert task not in anchor
