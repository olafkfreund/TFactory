"""Periodic completion-event outbox relay — the web-server driver for #281.

The backend ships the durable outbox + one-shot relay
(``agents.completion_outbox``); this is the long-running driver that drains the
outbox on a timer while the portal is up. It guarantees **at-least-once**
delivery of RFC-0001 completion events: an event durably enqueued by the
Triager is replayed here until CFactory's webhook returns 2xx, surviving
crashes and transient sink outages.

Started from the app lifespan and gated by ``APP_COMPLETION_RELAY_ENABLED``
(default OFF). The periodic body is factored into ``run_one_relay`` so it can
be unit-tested without spawning the loop; the sync relay runs in a thread so it
never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# The web-server's PYTHONPATH doesn't reliably include apps/backend, so add it
# explicitly at import time — the canonical pattern across the server.
_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from agents.completion_outbox import relay_once  # noqa: E402  (after sys.path insert)

# The completion-envelope schema version is owned by the backend's
# single-source-of-truth module (#360), derived from the vendored JSON schema.
# The web-server imports it rather than re-declaring a literal so the relayed
# events and the producer can never report a drifting ``schema_version``.
from agents.completion_schema import (  # noqa: E402  (after sys.path insert)
    COMPLETION_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


def completion_schema_version() -> str:
    """The completion-envelope schema version the web-server relays.

    Imported from the backend's single source of truth (#360) — exposed here so
    the web-server reports the *same* version the producer stamps, with a test
    asserting parity across the two apps.
    """
    return COMPLETION_SCHEMA_VERSION


def run_one_relay() -> dict:
    """Run a single relay pass and log the outcome. Testable, sync.

    Returns the relay stats dict (delivered / failed / dead_lettered / skipped).
    """
    stats = relay_once()
    return stats.as_dict()


async def completion_relay_loop(interval_seconds: float) -> None:
    """Run :func:`run_one_relay` every ``interval_seconds`` until cancelled.

    The sync relay runs in a worker thread so network/filesystem I/O never
    blocks the event loop. Per-iteration errors are logged and swallowed so a
    transient sink outage can't kill the loop; cancellation propagates cleanly
    on shutdown.
    """
    logger.info("completion relay loop started (every %ss)", interval_seconds)
    try:
        while True:
            try:
                await asyncio.to_thread(run_one_relay)
            except Exception:  # noqa: BLE001 - a bad iteration must not kill the loop
                logger.exception("completion relay iteration failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("completion relay loop cancelled")
        raise
