"""Coverage trend ledger + drift — RFC-0018 #486 (part 1).

Persists project-level coverage as a time series (one point per regression run)
and computes drift against the trailing baseline so a coverage drop can be
flagged in the report. Pure data + atomic JSON I/O with an injected path seam,
mirroring the other regression stores; the orchestrator records a point per run
in a later #486 slice.

Store format at ``<reg_dir>/coverage_trend.json``::

    {"points": [{"run_id": "r1", "ran_at": "...", "coverage_pct": 81.5,
                 "commit": "abc"}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TREND_FILE = "coverage_trend.json"
# Keep the trend bounded; old points beyond this are dropped (oldest first).
DEFAULT_WINDOW = 100
# Default drift threshold in percentage points: a drop larger than this flags.
DEFAULT_DRIFT_THRESHOLD = 2.0


@dataclass(frozen=True)
class CoveragePoint:
    """One project-coverage measurement at a regression run."""

    run_id: str
    ran_at: str
    coverage_pct: float
    commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ran_at": self.ran_at,
            "coverage_pct": self.coverage_pct,
            "commit": self.commit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoveragePoint:
        commit = d.get("commit")
        return cls(
            run_id=str(d["run_id"]),
            ran_at=str(d["ran_at"]),
            coverage_pct=float(d["coverage_pct"]),
            commit=None if commit is None else str(commit),
        )


@dataclass(frozen=True)
class DriftResult:
    """The coverage drift of the current run vs the trailing baseline."""

    current_pct: float
    baseline_pct: float | None
    delta: float | None  # current - baseline (None when no baseline yet)
    dropped: bool  # True when delta < -threshold
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_pct": self.current_pct,
            "baseline_pct": self.baseline_pct,
            "delta": None if self.delta is None else round(self.delta, 4),
            "dropped": self.dropped,
            "threshold": self.threshold,
        }


def coverage_trend_path(reg_dir: Path) -> Path:
    """Path to the per-project coverage-trend ledger inside *reg_dir*."""
    return Path(reg_dir) / _TREND_FILE


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    points = data.get("points") if isinstance(data, dict) else None
    return points if isinstance(points, list) else []


def _write(path: Path, points: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"points": points}, indent=2))
    tmp.replace(path)


def load_trend(path: Path) -> list[CoveragePoint]:
    """Return the recorded coverage points (chronological, oldest first)."""
    return [CoveragePoint.from_dict(d) for d in _read(path)]


def record_coverage(
    path: Path, point: CoveragePoint, *, window: int = DEFAULT_WINDOW
) -> list[CoveragePoint]:
    """Append *point* to the ledger (bounded to *window*); persist atomically."""
    points = [*_read(path), point.to_dict()][-window:]
    _write(path, points)
    return [CoveragePoint.from_dict(d) for d in points]


def compute_drift(
    trend: list[CoveragePoint],
    current_pct: float,
    *,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> DriftResult:
    """Drift of *current_pct* vs the most recent recorded point.

    With no prior point there is no baseline (``delta`` None, ``dropped`` False).
    """
    baseline = trend[-1].coverage_pct if trend else None
    delta = None if baseline is None else current_pct - baseline
    dropped = delta is not None and delta < -threshold
    return DriftResult(
        current_pct=current_pct,
        baseline_pct=baseline,
        delta=delta,
        dropped=dropped,
        threshold=threshold,
    )
