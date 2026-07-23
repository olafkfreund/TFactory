"""Tests for the liveness watchdog (#95).

The watchdog must flip a genuinely-silent active stage to ``stalled`` while
never clobbering a settled or just-finished task (fail-safe allowlist).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from agents.liveness import (
    ACTIVE_STATUSES,
    StallVerdict,
    check_and_mark,
    evaluate_liveness,
    mark_stalled,
)

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_DEADLINE = 900.0


def _write_status(spec_dir: Path, **fields: object) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(json.dumps(fields))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ── evaluate_liveness: the stalled case ─────────────────────────────────────


def test_active_and_stale_is_stalled(tmp_path: Path) -> None:
    _write_status(
        tmp_path,
        status="generating",
        phase="gen_subtask_3",
        updated_at=_iso(_NOW - timedelta(seconds=1800)),
    )
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is True
    assert v.status == "generating"
    assert v.idle_seconds == pytest.approx(1800)
    assert not v.ok


@pytest.mark.parametrize("status", sorted(ACTIVE_STATUSES))
def test_every_active_status_can_stall(tmp_path: Path, status: str) -> None:
    _write_status(
        tmp_path, status=status, updated_at=_iso(_NOW - timedelta(seconds=5000))
    )
    assert evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE).stalled


def test_stuck_reviewing_is_stalled_and_flips(tmp_path: Path) -> None:
    """RFC-0008 §3.3b (#423): a review-phase agent that dies leaves status at
    ``reviewing``; the watchdog must catch it instead of hanging forever."""
    assert "reviewing" in ACTIVE_STATUSES
    _write_status(
        tmp_path,
        status="reviewing",
        phase="review_ai_started",
        updated_at=_iso(_NOW - timedelta(seconds=1800)),
    )
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is True and v.status == "reviewing"
    assert check_and_mark(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    after = json.loads((tmp_path / "status.json").read_text())
    assert after["status"] == "stalled" and after["stalled_from"] == "reviewing"


# ── evaluate_liveness: the non-stalled / fail-safe cases ────────────────────


def test_active_but_fresh_is_not_stalled(tmp_path: Path) -> None:
    _write_status(
        tmp_path, status="evaluating", updated_at=_iso(_NOW - timedelta(seconds=60))
    )
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is False
    assert v.idle_seconds == pytest.approx(60)


@pytest.mark.parametrize(
    "status",
    ["planned", "generated", "evaluated", "triaged", "triager_failed", "stalled"],
)
def test_non_active_status_never_stalls(tmp_path: Path, status: str) -> None:
    # Handoff + terminal + already-stalled states are excluded even when stale.
    _write_status(
        tmp_path, status=status, updated_at=_iso(_NOW - timedelta(days=1))
    )
    assert not evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE).stalled


def test_missing_status_file_is_not_stalled(tmp_path: Path) -> None:
    assert not evaluate_liveness(tmp_path, now=_NOW).stalled


def test_unparseable_status_is_not_stalled(tmp_path: Path) -> None:
    spec = tmp_path
    spec.mkdir(parents=True, exist_ok=True)
    (spec / "status.json").write_text("{not json")
    assert not evaluate_liveness(spec, now=_NOW).stalled


def test_missing_updated_at_is_not_stalled(tmp_path: Path) -> None:
    _write_status(tmp_path, status="triaging")  # no updated_at
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is False
    assert v.idle_seconds is None


def test_naive_timestamp_treated_as_utc(tmp_path: Path) -> None:
    # A naive ISO string (no tz) must not raise and is read as UTC.
    naive = (_NOW - timedelta(seconds=2000)).replace(tzinfo=None).isoformat()
    _write_status(tmp_path, status="planning", updated_at=naive)
    assert evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE).stalled


def test_deadline_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_status(
        tmp_path, status="generating", updated_at=_iso(_NOW - timedelta(seconds=120))
    )
    # Default 900s → not stalled; tighten to 60s → stalled.
    assert not evaluate_liveness(tmp_path, now=_NOW).stalled
    monkeypatch.setenv("TFACTORY_STALL_DEADLINE_SECONDS", "60")
    assert evaluate_liveness(tmp_path, now=_NOW).stalled


# ── mark_stalled ────────────────────────────────────────────────────────────


def test_mark_stalled_flips_and_preserves_prior(tmp_path: Path) -> None:
    _write_status(
        tmp_path, status="evaluating", phase="signals",
        updated_at=_iso(_NOW - timedelta(seconds=2000)),
    )
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert mark_stalled(tmp_path, v, now=_NOW) is True

    after = json.loads((tmp_path / "status.json").read_text())
    assert after["status"] == "stalled"
    assert after["stalled_from"] == "evaluating"
    assert after["phase"] == "watchdog_stalled"
    assert after["stall_idle_seconds"] == 2000


@pytest.mark.parametrize("prior", ["planning", "generating"])
def test_mark_stalled_takes_inline_stages_terminal_failed(tmp_path: Path, prior) -> None:
    """An inline stage (planning/generating) runs in the control-plane process,
    so a stall is unrecoverable — it goes terminal `failed`, not `stalled`, so it
    leaves the cockpit's LIVE AGENTS instead of lingering as a fake live agent."""
    _write_status(
        tmp_path, status=prior, phase="x",
        updated_at=_iso(_NOW - timedelta(seconds=2000)),
    )
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert mark_stalled(tmp_path, v, now=_NOW) is True

    after = json.loads((tmp_path / "status.json").read_text())
    assert after["status"] == "failed"
    assert after["failed_from"] == prior
    assert after["phase"] == "watchdog_failed_inline"
    assert "#774" in after["failed_reason"]
    assert after["stall_idle_seconds"] == 2000


def test_mark_stalled_noop_when_not_stalled(tmp_path: Path) -> None:
    _write_status(tmp_path, status="evaluating", updated_at=_iso(_NOW))
    v = evaluate_liveness(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is False
    assert mark_stalled(tmp_path, v, now=_NOW) is False


def test_mark_stalled_does_not_clobber_advanced_stage(tmp_path: Path) -> None:
    # Verdict says stalled, but the stage has since advanced to a terminal
    # status — the flip must be skipped (no clobber).
    stale_verdict = StallVerdict(
        stalled=True, status="triaging", phase="x", idle_seconds=2000.0, reason="t"
    )
    _write_status(tmp_path, status="triaged", updated_at=_iso(_NOW))
    assert mark_stalled(tmp_path, stale_verdict, now=_NOW) is False
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "triaged"


# ── check_and_mark (driver convenience) ─────────────────────────────────────


def test_check_and_mark_flips_a_stalled_task(tmp_path: Path) -> None:
    _write_status(
        tmp_path, status="triaging", updated_at=_iso(_NOW - timedelta(seconds=3600))
    )
    v = check_and_mark(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is True
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "stalled"


def test_check_and_mark_leaves_healthy_task(tmp_path: Path) -> None:
    _write_status(
        tmp_path, status="triaging", updated_at=_iso(_NOW - timedelta(seconds=10))
    )
    v = check_and_mark(tmp_path, now=_NOW, deadline_seconds=_DEADLINE)
    assert v.stalled is False
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "triaging"
