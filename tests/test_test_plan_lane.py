"""Tests for the Lane enum + Subtask.lane field — Task 3 (#4).

Covers sub-task 3.1 model side: lane enum has the five expected values,
Subtask defaults to FUNCTIONAL, lane round-trips through to_dict/from_dict,
status transitions still work, and the backwards-compat alias survives.
"""

from __future__ import annotations

import pytest

from test_plan import (
    Lane,
    Subtask,
    SubtaskStatus,
)


# ── Lane enum ────────────────────────────────────────────────────────────


def test_lane_has_five_mvp_values() -> None:
    assert {l.value for l in Lane} == {
        "functional", "sast", "dast", "fuzz", "mutation",
    }


def test_lane_is_string_enum() -> None:
    # str subclass so it serialises naturally and JSON-encodes via .value
    assert isinstance(Lane.FUNCTIONAL, str)
    assert Lane.FUNCTIONAL == "functional"


# ── Subtask defaults ─────────────────────────────────────────────────────


def test_subtask_defaults_to_functional() -> None:
    s = Subtask(id="1", description="write a test")
    assert s.lane == Lane.FUNCTIONAL


def test_subtask_accepts_explicit_lane() -> None:
    s = Subtask(id="1", description="scan", lane=Lane.SAST)
    assert s.lane == Lane.SAST


# ── Round-trip via to_dict / from_dict ───────────────────────────────────


def test_to_dict_emits_lane() -> None:
    s = Subtask(id="1", description="x", lane=Lane.DAST)
    assert s.to_dict()["lane"] == "dast"


def test_from_dict_parses_lane() -> None:
    d = {"id": "1", "description": "x", "lane": "mutation"}
    s = Subtask.from_dict(d)
    assert s.lane == Lane.MUTATION


def test_from_dict_defaults_lane_when_missing() -> None:
    """Inherited JSON without a lane field round-trips as FUNCTIONAL."""
    d = {"id": "1", "description": "x"}  # no lane key — legacy shape
    s = Subtask.from_dict(d)
    assert s.lane == Lane.FUNCTIONAL


@pytest.mark.parametrize("lane", list(Lane))
def test_round_trip_all_lanes(lane: Lane) -> None:
    s = Subtask(id="1", description="x", lane=lane)
    again = Subtask.from_dict(s.to_dict())
    assert again.lane == lane


def test_from_dict_rejects_unknown_lane() -> None:
    with pytest.raises(ValueError):
        Subtask.from_dict({"id": "1", "description": "x", "lane": "nope"})


# ── Status transitions still work with lane present ──────────────────────


def test_lifecycle_methods_preserve_lane() -> None:
    s = Subtask(id="1", description="x", lane=Lane.MUTATION)
    s.start(session_id=42)
    assert s.lane == Lane.MUTATION
    assert s.status == SubtaskStatus.IN_PROGRESS

    s.complete(output="ok")
    assert s.lane == Lane.MUTATION
    assert s.status == SubtaskStatus.COMPLETED


# ── Backwards compat ─────────────────────────────────────────────────────


def test_chunk_alias_is_subtask() -> None:
    """The Chunk backwards-compat alias must continue to exist."""
    from test_plan import Chunk
    assert Chunk is Subtask
