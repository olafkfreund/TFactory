"""Liveness watchdog (#95) — flag a silent in-flight stage as ``stalled``.

A stage that hangs (the documented "Stream closed" failure) leaves
``status.json`` in an active "-ing" state with a frozen ``updated_at``: the
status claims an agent is working, but no further writes ever happen, so the
task hangs forever and the portal shows it "stuck". This module turns that
frozen timestamp into an explicit ``stalled`` status a watcher (or the portal)
can act on.

Pure compute + a best-effort writer:
  - ``evaluate_liveness`` reads ``status.json`` and compares ``updated_at``
    against an injected ``now`` — no side effects, fully unit-testable.
  - ``mark_stalled`` flips the status (best-effort) and emits a #95 stage
    event so a watcher learns immediately.
  - ``check_and_mark`` is the convenience the periodic driver calls.

Only the four *active* statuses can stall — handoff states
(planned/generated/evaluated) and terminal/failed states are deliberately
excluded so an already-settled task is never clobbered. The periodic driver
that calls this on a timer (web-server loop / cron) is the caller's concern.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "ACTIVE_STATUSES",
    "StallVerdict",
    "check_and_mark",
    "evaluate_liveness",
    "mark_stalled",
]

# Only these "an agent is actively running" statuses can stall. Handoff states
# (planned/generated/evaluated/replan_needed) and terminal/failed states
# (reviewed/review_failed/triaged/...) are excluded on purpose — flipping them
# would risk false positives (auto-fire may be off, or the task is already
# settled). ``reviewing`` is included (RFC-0008 §3.3b, #423): the review-phase
# agent (review_lane) sets status=reviewing, and a dead review subprocess used
# to leave the task at ``reviewing`` forever because the watchdog never watched
# it — the exact taskboard-demo hang.
ACTIVE_STATUSES = frozenset(
    {"planning", "generating", "evaluating", "triaging", "reviewing"}
)

# The subset of active statuses whose stages run IN the control-plane process
# (not a k8s Job). A stall here is unrecoverable — the owning process is gone —
# so mark_stalled takes these terminal (`failed`) rather than `stalled`.
_INLINE_STALL_STATUSES = frozenset({"planning", "generating"})

# Which agent owns each active status — used to label the emitted stage event.
_STATUS_TO_STAGE = {
    "planning": "planner",
    "generating": "gen_functional",
    "evaluating": "evaluator",
    "triaging": "triager",
    "reviewing": "review",
}

# Default idle budget before an active stage is considered stalled. Generous —
# real stages (LLM calls, 3x stability re-runs, Docker) can be legitimately
# quiet for a while. Override with TFACTORY_STALL_DEADLINE_SECONDS.
DEFAULT_DEADLINE_SECONDS = 900  # 15 min


def _deadline_seconds() -> float:
    raw = os.environ.get("TFACTORY_STALL_DEADLINE_SECONDS")
    if raw is None:
        return float(DEFAULT_DEADLINE_SECONDS)
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_DEADLINE_SECONDS)


def _now_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Our writers are tz-aware, but treat a naive timestamp as UTC just in case.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class StallVerdict:
    """The watchdog's read of one task. ``stalled`` drives the flip."""

    stalled: bool
    status: str | None  # the status observed (None when unreadable)
    phase: str | None
    idle_seconds: float | None  # None when updated_at is missing/unparseable
    reason: str

    @property
    def ok(self) -> bool:
        """True when the task is NOT stalled (healthy or not-applicable)."""
        return not self.stalled


def evaluate_liveness(
    spec_dir: Path,
    *,
    now: datetime,
    deadline_seconds: float | None = None,
) -> StallVerdict:
    """Decide whether the task at ``spec_dir`` has stalled — no side effects.

    Stalled ⇔ ``status`` is in :data:`ACTIVE_STATUSES` AND ``status.json``'s
    ``updated_at`` is older than ``deadline_seconds`` before ``now``.

    A missing/corrupt ``status.json``, a non-active status, or an unparseable
    ``updated_at`` all yield ``stalled=False`` — fail-safe; never flip on
    ambiguous input.
    """
    deadline = _deadline_seconds() if deadline_seconds is None else deadline_seconds
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return StallVerdict(False, None, None, None, "no status.json")
    try:
        status = json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return StallVerdict(False, None, None, None, "status.json unreadable")
    if not isinstance(status, dict):
        return StallVerdict(False, None, None, None, "status.json not an object")

    st = status.get("status")
    phase = status.get("phase")
    if st not in ACTIVE_STATUSES:
        return StallVerdict(False, st, phase, None, f"status '{st}' is not active")

    updated = _parse_iso(status.get("updated_at"))
    if updated is None:
        return StallVerdict(False, st, phase, None, "updated_at missing/unparseable")

    idle = (now - updated).total_seconds()
    if idle > deadline:
        return StallVerdict(
            True,
            st,
            phase,
            idle,
            f"active '{st}' idle {idle:.0f}s > {deadline:.0f}s deadline",
        )
    return StallVerdict(False, st, phase, idle, f"active '{st}' idle {idle:.0f}s")


def mark_stalled(spec_dir: Path, verdict: StallVerdict, *, now: datetime) -> bool:
    """Flip a stalled task out of its active status (best-effort). Returns success.

    Re-reads ``status.json`` and only flips if it is *still* in an active
    status — so a stage that finished between ``evaluate_liveness`` and here is
    never clobbered. Emits a #95 stage event so a watcher learns immediately.

    Inline stages (:data:`_INLINE_STALL_STATUSES` — planning/generating) run in
    the control-plane process, not a k8s Job: a stall there means the owning
    process is gone (a roll/OOM/drain) with nothing to resume it, so the spec
    goes **terminal ``failed``** rather than sitting in a ``stalled`` limbo that
    the cockpit surfaces as a live agent (the #742/#774 contract). Job-backed
    stages (evaluating/triaging/reviewing) can genuinely recover, so they stay
    ``stalled`` for a watcher/reaper to act on.
    """
    if not verdict.stalled:
        return False
    status_path = spec_dir / "status.json"
    try:
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(status, dict) or status.get("status") not in ACTIVE_STATUSES:
        # The stage advanced since the verdict was computed — leave it alone.
        return False

    prior = status.get("status")
    idle = round(verdict.idle_seconds or 0.0)
    if prior in _INLINE_STALL_STATUSES:
        status["status"] = "failed"
        status["failed_from"] = prior
        status["phase"] = "watchdog_failed_inline"
        status["failed_reason"] = (
            f"control-plane {prior} stage went idle {idle}s past the deadline; the "
            "in-process session is gone with no Job to reconcile (#774)"
        )
    else:
        status["status"] = "stalled"
        status["stalled_from"] = prior
        status["phase"] = "watchdog_stalled"
    status["stall_idle_seconds"] = idle
    status["updated_at"] = _now_iso(now)
    try:
        status_path.write_text(json.dumps(status, indent=2))
    except OSError:
        return False

    # Surface immediately to any #95 watcher (best-effort; never raises).
    try:
        from agents.stage_events import emit_stage_event

        emit_stage_event(
            spec_dir, status, stage=_STATUS_TO_STAGE.get(prior or "", "unknown")
        )
    except Exception:
        pass
    return True


def check_and_mark(
    spec_dir: Path,
    *,
    now: datetime | None = None,
    deadline_seconds: float | None = None,
) -> StallVerdict:
    """Evaluate liveness and flip to ``stalled`` if needed. Driver convenience.

    Returns the :class:`StallVerdict` so the caller can log/act. ``now``
    defaults to the current UTC time.
    """
    when = now or datetime.now(timezone.utc)
    verdict = evaluate_liveness(spec_dir, now=when, deadline_seconds=deadline_seconds)
    if verdict.stalled:
        mark_stalled(spec_dir, verdict, now=when)
    return verdict
