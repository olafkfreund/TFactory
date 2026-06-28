"""Regression quarantine store — RFC-0018 #485 (part 1).

Persists the set of *quarantined* tests for a project: tests excluded from the
regression gate because they are chronically flaky (decided by the quarantine
policy, a later #485 slice). A quarantined test is still run and reported — it
just doesn't fail the gate — and an operator can release it.

Pure data + atomic JSON I/O with an injected path seam, mirroring
``flaky_history`` / the regression ``store``. The store lives alongside the
runs at ``<reg_dir>/quarantine.json``.

Store format::

    {"<test_id>": {"reason": "...", "since_run": "run-...", "flip_rate": 0.4}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.regression._io import write_json

_QUARANTINE_FILE = "quarantine.json"


@dataclass(frozen=True)
class QuarantineEntry:
    """A quarantined test and why it was quarantined."""

    test_id: str
    reason: str
    since_run: str  # run_id at which it was quarantined
    flip_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "since_run": self.since_run,
            "flip_rate": self.flip_rate,
        }

    @classmethod
    def from_dict(cls, test_id: str, d: dict[str, Any]) -> QuarantineEntry:
        fr = d.get("flip_rate")
        return cls(
            test_id=test_id,
            reason=str(d.get("reason", "")),
            since_run=str(d.get("since_run", "")),
            flip_rate=None if fr is None else float(fr),
        )


def quarantine_path(reg_dir: Path) -> Path:
    """Path to the per-project quarantine store inside *reg_dir*."""
    return Path(reg_dir) / _QUARANTINE_FILE


def _read(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_quarantine(path: Path) -> dict[str, QuarantineEntry]:
    """Return all quarantine entries keyed by ``test_id``."""
    return {tid: QuarantineEntry.from_dict(tid, d) for tid, d in _read(path).items()}


def quarantined_ids(path: Path) -> frozenset[str]:
    """Return the set of currently-quarantined ``test_id``s."""
    return frozenset(_read(path))


def is_quarantined(path: Path, test_id: str) -> bool:
    return test_id in _read(path)


def add_to_quarantine(path: Path, entry: QuarantineEntry) -> None:
    """Quarantine *entry*'s test (idempotent upsert; persisted atomically)."""
    data = _read(path)
    data[entry.test_id] = entry.to_dict()
    write_json(path, data)


def release_from_quarantine(path: Path, test_id: str) -> bool:
    """Operator action: un-quarantine *test_id*. Returns True if it was present."""
    data = _read(path)
    if test_id not in data:
        return False
    del data[test_id]
    write_json(path, data)
    return True
