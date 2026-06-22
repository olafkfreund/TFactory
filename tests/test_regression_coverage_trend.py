"""Tests for the coverage trend ledger + drift — RFC-0018 #486 (part 1)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CoveragePoint,
    compute_drift,
    coverage_trend_path,
    load_trend,
    record_coverage,
    regression_dir,
)


def _path(tmp_path: Path) -> Path:
    return coverage_trend_path(regression_dir(tmp_path, "demo"))


def _pt(run_id: str, pct: float) -> CoveragePoint:
    return CoveragePoint(run_id=run_id, ran_at="2026-06-22T12:00:00Z", coverage_pct=pct)


# ── ledger ─────────────────────────────────────────────────────────────
def test_empty_trend(tmp_path):
    assert load_trend(_path(tmp_path)) == []


def test_record_and_roundtrip(tmp_path):
    p = _path(tmp_path)
    record_coverage(p, _pt("r1", 80.0))
    record_coverage(p, CoveragePoint("r2", "2026-06-22T13:00:00Z", 82.5, commit="abc"))
    trend = load_trend(p)
    assert [pt.run_id for pt in trend] == ["r1", "r2"]  # chronological
    assert trend[1].coverage_pct == 82.5
    assert trend[1].commit == "abc"


def test_window_bounds_history(tmp_path):
    p = _path(tmp_path)
    for i in range(5):
        record_coverage(p, _pt(f"r{i}", 70.0 + i), window=3)
    trend = load_trend(p)
    assert [pt.run_id for pt in trend] == ["r2", "r3", "r4"]  # newest 3 kept


# ── drift ─────────────────────────────────────────────────────────────
def test_drift_no_baseline():
    d = compute_drift([], 80.0)
    assert d.baseline_pct is None
    assert d.delta is None
    assert d.dropped is False


def test_drift_drop_flagged():
    trend = [_pt("r1", 85.0)]
    d = compute_drift(trend, 80.0, threshold=2.0)
    assert d.baseline_pct == 85.0
    assert d.delta == -5.0
    assert d.dropped is True


def test_drift_small_drop_not_flagged():
    d = compute_drift([_pt("r1", 85.0)], 84.0, threshold=2.0)
    assert d.delta == -1.0
    assert d.dropped is False


def test_drift_increase_not_flagged():
    d = compute_drift([_pt("r1", 80.0)], 83.0, threshold=2.0)
    assert d.delta == 3.0
    assert d.dropped is False


def test_drift_uses_latest_point_as_baseline():
    trend = [_pt("r1", 90.0), _pt("r2", 70.0)]
    d = compute_drift(trend, 71.0, threshold=2.0)
    assert d.baseline_pct == 70.0  # latest, not the first
    assert d.dropped is False


def test_drift_to_dict_rounds():
    d = compute_drift([_pt("r1", 80.0)], 77.333333, threshold=2.0)
    out = d.to_dict()
    assert out["dropped"] is True
    assert out["delta"] == round(77.333333 - 80.0, 4)
