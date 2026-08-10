"""Shared spec-workspace status helpers (factory-agents dedup, #451).

Several agent modules (``planner.py``, ``evaluator.py``, ``triager.py``,
``gen_functional.py``, ``review_lane.py`` and friends) independently
reimplemented the same small primitives for reading and patching a spec's
``status.json``:

- ``now_iso`` — tz-aware ISO-8601 timestamp (seconds resolution).
- ``read_status`` / ``write_status_patch`` — read and merge-patch a spec's
  ``status.json``, emitting a best-effort stage event on every write (#95).
- ``truthy`` — the canonical env-var truthiness check.

This module is the single home for those, so every agent shares one
implementation instead of copying it. The behaviour is identical to the
previous per-module copies; callers keep their own ``stage`` discriminator by
passing it in. This mirrors PFactory's ``agents/agent_infra.py`` (#205); the
eventual goal is to consume the shared ``factory-agents`` module directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

_log = logging.getLogger(__name__)

__all__ = [
    "anchor_stage_task",
    "now_iso",
    "read_status",
    "truthy",
    "write_status_patch",
]


def now_iso() -> str:
    """Tz-aware ISO-8601 timestamp at seconds resolution."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def truthy(env_val: str | None) -> bool:
    """Canonical env-var truthiness check ("1"/"true"/"yes"/"on")."""
    if env_val is None:
        return False
    return env_val.strip().lower() in ("1", "true", "yes", "on")


def read_status(spec_dir: Path) -> dict[str, Any]:
    """Read ``status.json`` or return an empty dict if missing/corrupt."""
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        parsed: dict[str, Any] = json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return parsed


def write_status_patch(spec_dir: Path, stage: str, **fields: object) -> None:
    """Merge ``fields`` into ``status.json`` (atomic-ish single-file write).

    ``stage`` is the discriminator forwarded to the best-effort push-based
    progress event (#95); the event is a no-op unless opted in via env var.
    """
    status = read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Lazy import: stage_events imports this module at top-level, so importing
    # it here at top-level would be circular.
    from agents.stage_events import (  # noqa: PLC0415
        emit_stage_event,
    )

    emit_stage_event(spec_dir, status, stage=stage)


def anchor_stage_task(
    task: asyncio.Task[Any],
    anchor_set: set[asyncio.Task[Any]],
    *,
    spec_dir: Path,
    stage: str,
    failed_status: str,
) -> asyncio.Task[Any]:
    """Anchor a fire-and-forget stage task AND capture its failure (#714).

    The verify stages fire the next stage as a detached asyncio task, anchoring
    it in a module-level set so it isn't GC'd mid-flight. The old done-callback
    only discarded the anchor — so an unexpected exception in the background
    stage (one the stage's own ``try/except`` didn't catch: a crash inside its
    ``except`` handler, or before its guard) was SILENTLY swallowed. The spec
    then dead-ended at the previous status with no verdict and no log — exactly
    the #714 "stops after review, no Evaluator/Triager, no VAL verdict" stall.

    Capture it instead: log the traceback and record ``status=<failed_status>``
    with the reason, so a crashing stage fails LOUD (a real terminal status the
    reaper/cockpit can see) rather than stranding the verify. A cancelled task
    (event-loop shutdown) is not a failure — it is only discarded.
    """
    anchor_set.add(task)

    def _on_done(finished: asyncio.Task[Any]) -> None:
        anchor_set.discard(finished)
        try:
            exc = finished.exception()
        except asyncio.CancelledError:
            return  # loop shutdown, not a stage failure
        if exc is None:
            return
        _log.error("stage %s task crashed: %r", stage, exc, exc_info=exc)
        try:
            write_status_patch(
                spec_dir,
                stage,
                status=failed_status,
                **{f"{stage}_error": f"{stage} task crashed: {exc!r}"[:500]},
            )
        except Exception:  # noqa: BLE001 — the safety net must never re-raise
            _log.exception("failed to record %s crash status", stage)

    task.add_done_callback(_on_done)
    return task
