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

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
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
