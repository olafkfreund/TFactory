"""Tests for the regression quarantine store — RFC-0018 #485 (part 1)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    QuarantineEntry,
    add_to_quarantine,
    is_quarantined,
    load_quarantine,
    quarantine_path,
    quarantined_ids,
    regression_dir,
    release_from_quarantine,
)


def _path(tmp_path: Path) -> Path:
    return quarantine_path(regression_dir(tmp_path, "demo"))


def test_empty_store_is_safe(tmp_path):
    p = _path(tmp_path)
    assert load_quarantine(p) == {}
    assert quarantined_ids(p) == frozenset()
    assert is_quarantined(p, "anything") is False
    assert release_from_quarantine(p, "anything") is False


def test_add_and_roundtrip(tmp_path):
    p = _path(tmp_path)
    add_to_quarantine(
        p,
        QuarantineEntry(
            test_id="flip",
            reason="flip_rate 0.4 >= 0.25",
            since_run="r3",
            flip_rate=0.4,
        ),
    )
    assert is_quarantined(p, "flip")
    assert quarantined_ids(p) == frozenset({"flip"})
    entry = load_quarantine(p)["flip"]
    assert entry.reason == "flip_rate 0.4 >= 0.25"
    assert entry.since_run == "r3"
    assert entry.flip_rate == 0.4


def test_add_is_idempotent_upsert(tmp_path):
    p = _path(tmp_path)
    add_to_quarantine(p, QuarantineEntry("t", "first", "r1"))
    add_to_quarantine(p, QuarantineEntry("t", "second", "r2", flip_rate=0.3))
    ids = quarantined_ids(p)
    assert ids == frozenset({"t"})  # not duplicated
    assert load_quarantine(p)["t"].reason == "second"  # latest wins


def test_release(tmp_path):
    p = _path(tmp_path)
    add_to_quarantine(p, QuarantineEntry("a", "r", "r1"))
    add_to_quarantine(p, QuarantineEntry("b", "r", "r1"))
    assert release_from_quarantine(p, "a") is True
    assert is_quarantined(p, "a") is False
    assert quarantined_ids(p) == frozenset({"b"})
    # releasing again is a no-op returning False
    assert release_from_quarantine(p, "a") is False


def test_entry_dict_roundtrip_without_flip_rate(tmp_path):
    p = _path(tmp_path)
    add_to_quarantine(p, QuarantineEntry("t", "manual", "r1"))
    e = load_quarantine(p)["t"]
    assert e.flip_rate is None
    assert e == QuarantineEntry("t", "manual", "r1")
