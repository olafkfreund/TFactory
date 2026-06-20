"""Native-status → canonical lifecycle-state mapping for job-state rows (RFC-0016).

The durable job-state store (``job_state_store.py``) records each verify task's
raw service-native status (e.g. ``triaged``, ``reviewing``, ``review_failed``,
or the ``review_initial_complete`` phase) alongside a *canonical*
``lifecycle_state`` drawn from the Factory hub's
``apis/status-taxonomy.json`` set: ``queued · running · review · done · failed ·
stuck``. The cockpit and any control-plane reconciler reason over the canonical
state; the raw string is kept for audit.

Mapping rules mirror the hub taxonomy EXACTLY (token-set membership, never loose
substring containment — so ``review_failed`` is *failed*, not *review*):

  - Tokenize: lowercase, strip, split on whitespace / ``_`` / ``-``.
  - A status is classified by the FIRST taxonomy state (checked in the
    precedence order below) that owns any of its tokens.
  - Precedence ``failed → done → review → queued → running → stuck`` resolves
    compound statuses deterministically (``review_failed`` → *failed*, because
    the ``failed`` token wins over the ``review`` token).
  - A present-but-unrecognized status reads as ``running`` (taxonomy
    ``running_fallback``): a just-started agent often has no canonical mark yet.
  - An empty / missing status reads as ``queued`` (no agent attached yet).

This module is the TFactory-side projection of the hub single source of truth;
if the hub token sets change, update the sets below to match.
"""

from __future__ import annotations

import re

# Canonical lifecycle states (job-state.schema.json `lifecycle_state` enum).
QUEUED = "queued"
RUNNING = "running"
REVIEW = "review"
DONE = "done"
FAILED = "failed"
STUCK = "stuck"

# Token sets copied verbatim from apis/status-taxonomy.json (the hub single
# source of truth). Keep in sync with that file.
_FAILED_TOKENS = frozenset(
    {
        "fail",
        "failed",
        "failure",
        "reject",
        "rejected",
        "block",
        "blocked",
        "error",
        "errored",
        "cancel",
        "cancelled",
        "canceled",
        "discard",
        "discarded",
        "abort",
        "aborted",
    }
)
_DONE_TOKENS = frozenset(
    {
        "done",
        "merged",
        "triaged",
        "emitted",
        "complete",
        "completed",
        "accept",
        "accepted",
        "passed",
        "approved",
        "shipped",
        "succeeded",
        "success",
        "ready",
        "closed",
        "skip",
        "skipped",
    }
)
_REVIEW_TOKENS = frozenset({"review", "reviewing", "awaiting"})
_QUEUED_TOKENS = frozenset({"backlog", "pending", "queued", "todo", "icebox", "draft"})
_RUNNING_TOKENS = frozenset(
    {
        "running",
        "executing",
        "working",
        "coding",
        "planning",
        "generating",
        "building",
        "act",
        "in_progress",
        "inprogress",
        "progress",
        "started",
    }
)
_STUCK_TOKENS = frozenset({"stuck", "stalled"})

# Precedence: a compound status is classified by the first state that claims a
# token. failed beats done beats review beats queued beats running beats stuck —
# so ``review_failed`` → failed and ``triaged_empty`` → done.
_PRECEDENCE: tuple[tuple[str, frozenset[str]], ...] = (
    (FAILED, _FAILED_TOKENS),
    (DONE, _DONE_TOKENS),
    (REVIEW, _REVIEW_TOKENS),
    (QUEUED, _QUEUED_TOKENS),
    (RUNNING, _RUNNING_TOKENS),
    (STUCK, _STUCK_TOKENS),
)

_SPLIT = re.compile(r"[\s_\-]+")

# Phases that mean "executed an initial pass, parked for the review/handback
# decision" — these read as REVIEW even though the raw status may be active.
# ``review_initial_complete`` is TFactory's flagship example (RFC-0008).
_REVIEW_PHASES = frozenset({"review_initial_complete", "awaiting_review"})


def _tokens(status: str) -> list[str]:
    return [t for t in _SPLIT.split(status.strip().lower()) if t]


def to_lifecycle_state(
    service_status: str | None,
    *,
    phase: str | None = None,
    has_verdict: bool = True,
) -> str:
    """Map a TFactory native status (+ optional phase) to a canonical lifecycle state.

    Args:
        service_status: the raw status string written to the spec's
            ``status.json`` (e.g. ``triaged``, ``reviewing``, ``review_failed``).
        phase: optional finer-grained phase; ``review_initial_complete`` and
            other review phases promote an otherwise-active status to ``review``.
        has_verdict: when False, a status that would otherwise read as terminal
            ``done`` (e.g. ``triaged`` with zero verdicts) is downgraded to
            ``stuck`` — the "lanes pending, no verdict" stall class (TFactory
            #464) the conventions require to be representable so a reconciler can
            reap it.

    Returns:
        One of ``queued``/``running``/``review``/``done``/``failed``/``stuck``.
    """
    # A review phase parks the task for a decision regardless of the active mark.
    if phase and phase.strip().lower() in _REVIEW_PHASES:
        return REVIEW

    if not service_status or not service_status.strip():
        return QUEUED

    tokens = set(_tokens(service_status))
    for state, token_set in _PRECEDENCE:
        if tokens & token_set:
            if state == DONE and not has_verdict:
                # Terminal-by-name but produced no verdict → no-verdict stall.
                return STUCK
            return state

    # Present but unrecognized → running (taxonomy running_fallback).
    return RUNNING


def is_terminal(lifecycle_state: str) -> bool:
    """A lifecycle state is terminal iff it is done or failed (per taxonomy)."""
    return lifecycle_state in (DONE, FAILED)


def is_active(lifecycle_state: str) -> bool:
    """Active = occupies an admission slot: queued or running (RFC-0016)."""
    return lifecycle_state in (QUEUED, RUNNING)
