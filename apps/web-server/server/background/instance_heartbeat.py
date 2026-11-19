"""Per-process heartbeat + inline-orphan reconcile loop (#1599).

Each replica proves it is alive by touching ``<workspace>/instances/<id>``; a peer
reaps an inline (planning/generating) stage only once its owner's heartbeat is
stale. Renewal and reconcile are SEPARATE tasks so a slow scan (it sends blocking
completion notifications) can never starve the heartbeat.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from agents.liveness_sweep import (  # noqa: E402  (after sys.path insert)
    beat,
    purge_old_instances,
    reconcile_inline_orphans,
)

logger = logging.getLogger(__name__)


async def heartbeat_loop(interval_seconds: float) -> None:
    """Beat every ``interval_seconds`` until cancelled; errors are logged, not fatal."""
    try:
        while True:
            try:
                await asyncio.to_thread(beat)
                await asyncio.to_thread(purge_old_instances)
            except Exception:  # noqa: BLE001 - a missed beat must not kill the loop
                logger.exception("#1599 instance heartbeat failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        raise


async def reconcile_loop(interval_seconds: float) -> None:
    """Reap inline stages whose owner's heartbeat is gone, every interval."""
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                failed = await asyncio.to_thread(reconcile_inline_orphans)
                if failed:
                    logger.warning(
                        "#1599 reconcile: failed %d inline stage(s) whose owner is gone: %s",
                        len(failed),
                        [f"{d.name}({prior})" for d, prior in failed],
                    )
            except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
                logger.exception("#1599 inline reconcile tick failed")
    except asyncio.CancelledError:
        raise
