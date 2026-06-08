"""Tests for the web-server completion-relay driver (#281).

Exercises the periodic-relay glue without spawning the unbounded loop: the
single-iteration body is called directly, and the loop is started then
cancelled to prove it shuts down cleanly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Add apps/web-server/ to sys.path so ``from server.background...`` resolves.
# When the web-server deps are absent (the backend-only test venv), conftest's
# pytest_ignore_collect skips this whole module; it runs under the web-server
# venv / CI. Same pattern as test_web_liveness_sweep.
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
if str(WEB_SERVER_PATH) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_PATH))

_BACKEND_PATH = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from server.background.completion_relay import (  # noqa: E402
    completion_relay_loop,
    run_one_relay,
)


def test_run_one_relay_drains_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    from agents import completion_outbox as ob

    ob.enqueue({"id": "evt-1", "status": "triaged"}, root=tmp_path / "outbox")

    # Sink accepts → entry delivered + removed.
    monkeypatch.setattr(ob, "_default_deliver", lambda env, i: True)
    stats = run_one_relay()
    assert stats["delivered"] == 1
    assert ob.pending(tmp_path / "outbox") == []


def test_run_one_relay_empty_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    stats = run_one_relay()
    assert stats == {
        "delivered": 0,
        "failed": 0,
        "dead_lettered": 0,
        "skipped": 0,
    }


@pytest.mark.asyncio
async def test_completion_relay_loop_cancels_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    task = asyncio.create_task(completion_relay_loop(0.01))
    await asyncio.sleep(0.03)  # let a couple iterations run
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
