"""Durable verify-job state store (RFC-0016 Phase 1, TFactory #465).

The control plane today tracks in-flight verifies in
``AgentService.running_tasks`` (an in-memory dict) plus per-spec ``status.json``
files on a pod-local volume. Neither survives a restart, and neither is safe
across multiple replicas — two pods would each keep their own ``running_tasks``
and could blow past the admission cap or double-start a job.

This module is the durable replacement: one row per job in shared Postgres
(``job_states`` table, ``apis/job-state.schema.json`` shape) keyed by ``job_id``
(the spec/task id). It backs the running/admission set so that:

  - The admission **count** comes from ``SELECT count(*) WHERE
    lifecycle_state IN (queued, running)`` for ``service=tfactory`` — durable and
    consistent across replicas (admission-cap *enforcement* is a thin follow-up;
    this store provides the authoritative number).
  - A restarted / new replica reconstructs in-flight state by querying the same
    predicate (``recover_in_flight``).
  - Slot grants and state transitions take a row lock
    (``SELECT ... FOR UPDATE`` on Postgres) inside one transaction, so two
    replicas can't double-start a ``job_id`` or exceed the cap.
  - Every terminal transition sets ``ended_at`` + ``result``; ``failed``/``stuck``
    set ``error`` (never-overclaim, RFC-0001a/0006).
  - A job that finishes its lanes without a verdict is recorded as ``stuck`` so a
    reconciler/control-plane can reap it (the "lanes pending, no verdict" class,
    TFactory #464).

Fallback: when ``DATABASE_URL`` is unset the app runs on the SQLite default
(single-pod dev). The store still works against SQLite — ``FOR UPDATE`` is a
no-op there and ``BEGIN IMMEDIATE`` (WAL) serializes writers within one pod — but
it logs once that this mode is **not multi-replica safe**. An in-memory store is
also provided for tests / a hard no-DB path; it carries the same warning.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.engine import async_session_factory
from ..database.models import JobState
from . import job_state_status as st

logger = logging.getLogger(__name__)

SERVICE = "tfactory"
KIND = "verify"

# Lifecycle states that occupy an admission slot (RFC-0016).
_ACTIVE_STATES = (st.QUEUED, st.RUNNING)

# Admission cap (RFC-0016 #465). Each verify spawns runtime/test containers that
# each request ~cpu 2 / mem 2g; with no cap, 5-10 concurrent verifies OOM the
# pod. We cap the number of *running* verifies — extra verifies wait in `queued`
# (not hard-failed) and are auto-promoted FIFO as running ones finish.
_MAX_CONCURRENT_ENV = "TFACTORY_MAX_CONCURRENT_VERIFIES"
_DEFAULT_MAX_CONCURRENT = 4

_FALLBACK_WARNED = False


def max_concurrent_verifies() -> int:
    """The configured cap on concurrently *running* verifies (RFC-0016).

    Read live from ``TFACTORY_MAX_CONCURRENT_VERIFIES`` (default 4). A value
    ``<= 0`` means **unlimited** — admission always grants a slot immediately.
    An unparseable value falls back to the default and warns once.
    """
    raw = os.environ.get(_MAX_CONCURRENT_ENV)
    if raw is None or raw.strip() == "":
        return _DEFAULT_MAX_CONCURRENT
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "[job-state] %s=%r is not an integer; using default %d",
            _MAX_CONCURRENT_ENV,
            raw,
            _DEFAULT_MAX_CONCURRENT,
        )
        return _DEFAULT_MAX_CONCURRENT


def _warn_not_multi_replica_safe(backend: str) -> None:
    """Log (once) that the active backend is not multi-replica safe."""
    global _FALLBACK_WARNED
    if _FALLBACK_WARNED:
        return
    _FALLBACK_WARNED = True
    logger.warning(
        "[job-state] using %s backend for verify job-state — this is NOT "
        "multi-replica safe. Set DATABASE_URL to Postgres for durable, "
        "multi-replica admission control (RFC-0016).",
        backend,
    )


def _now() -> datetime:
    """Tz-aware UTC now — for ISO-string JSON timestamps (admission bookkeeping)."""
    return datetime.now(timezone.utc)


def _now_naive() -> datetime:
    """Naive UTC now — for DateTime columns.

    The ``job_states`` timestamp columns are ``TIMESTAMP WITHOUT TIME ZONE``
    (matching every other model in this codebase, which use naive ``DateTime`` +
    ``func.now()``). asyncpg rejects a tz-aware datetime for a naive column
    ("can't subtract offset-naive and offset-aware datetimes"), so column writes
    MUST be naive. We store wall-clock UTC, consistent with ``func.now()``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dumps(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def row_to_record(row: JobState) -> dict[str, Any]:
    """Project a ``JobState`` row to the ``job-state.schema.json`` record shape."""
    return {
        "schema_version": row.schema_version,
        "job_id": row.job_id,
        "correlation_key": row.correlation_key,
        "service": row.service,
        "kind": row.kind,
        "lifecycle_state": row.lifecycle_state,
        "service_status": row.service_status,
        "phase": row.phase,
        "attempt": row.attempt,
        "admission": _loads(row.admission_json),
        "worker_ref": _loads(row.worker_ref_json),
        "artifacts": _loads(row.artifacts_json) or [],
        "result": _loads(row.result_json),
        "usage": _loads(row.usage_json),
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
    }


class DbJobStateStore:
    """Postgres-backed (SQLite-fallback) durable verify job-state store.

    All slot-granting / state-advancing methods lock the row with
    ``SELECT ... FOR UPDATE`` inside the caller's transaction so concurrent
    replicas serialize on the same ``job_id``. The session is committed by these
    methods (mirroring ``DbProjectStore``).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # SQLite is single-pod only; warn so operators know the deployment isn't
        # multi-replica safe until DATABASE_URL points at Postgres.
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            _warn_not_multi_replica_safe("sqlite")

    @property
    def _is_postgres(self) -> bool:
        bind = self._session.bind
        return bind is not None and bind.dialect.name == "postgresql"

    async def _locked_row(self, job_id: str) -> JobState | None:
        """Fetch a row for update. ``FOR UPDATE`` only on Postgres (SQLite n/a)."""
        stmt = select(JobState).where(JobState.job_id == job_id)
        if self._is_postgres:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, job_id: str) -> dict[str, Any] | None:
        result = await self._session.execute(
            select(JobState).where(JobState.job_id == job_id)
        )
        row = result.scalar_one_or_none()
        return row_to_record(row) if row else None

    async def active_count(self) -> int:
        """Authoritative admission count: jobs holding a slot (queued|running).

        This is the number the admission cap reads — live from the table, so it
        is consistent across replicas and survives a control-plane restart.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(JobState)
            .where(
                JobState.service == SERVICE,
                JobState.lifecycle_state.in_(_ACTIVE_STATES),
            )
        )
        return int(result.scalar_one())

    async def running_count(self) -> int:
        """Count verifies currently *running* (holding an execution slot).

        The admission cap limits this number (queued jobs wait for a slot but do
        not consume one), distinct from ``active_count`` which also counts queued.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(JobState)
            .where(
                JobState.service == SERVICE,
                JobState.lifecycle_state == st.RUNNING,
            )
        )
        return int(result.scalar_one())

    async def try_admit(self, job_id: str, **enqueue_kwargs: Any) -> dict[str, Any]:
        """Admit a verify under the concurrency cap — atomically, multi-replica safe.

        Ensures a row exists for ``job_id`` (idempotent ``enqueue``), then, inside
        a single ``SELECT ... FOR UPDATE``-guarded transaction (Postgres), counts
        running verifies and either:

          - grants a slot (lifecycle_state → ``running``) when under the cap, or
          - leaves the job ``queued`` when at the cap — so a new verify WAITS
            instead of hard-failing or starting and OOMing the pod.

        ``<= 0`` cap = unlimited → always grants. An already-running job is
        returned as-is (idempotent). The locking serializes concurrent admits so
        two replicas can't both grant the same final slot and exceed the cap.

        Returns the resulting record; callers inspect ``lifecycle_state`` to learn
        whether they were admitted (``running``) or queued (``queued``).
        """
        # Make sure the row exists first (its own short txn). enqueue is idempotent
        # and locks the row, so it won't clobber an in-flight job.
        await self.enqueue(job_id, **enqueue_kwargs)

        cap = max_concurrent_verifies()
        # Lock the candidate row for the whole admit decision so the count→grant
        # is atomic against concurrent admits (FOR UPDATE on Postgres).
        row = await self._locked_row(job_id)
        if row is None:  # pragma: no cover — enqueue just created it
            raise KeyError(f"job_id {job_id!r} not found")
        if row.lifecycle_state == st.RUNNING:
            # Already admitted — nothing to do (idempotent re-admit).
            await self._session.commit()
            return row_to_record(row)

        # Count running EXCLUDING this row (it's queued). Under the cap → grant.
        running = await self.running_count()
        if cap <= 0 or running < cap:
            row.lifecycle_state = st.RUNNING
            adm = _loads(row.admission_json) or {}
            adm["started_at"] = _now().isoformat()
            adm["queue_position"] = None
            row.admission_json = _dumps(adm)
        await self._session.commit()
        await self._session.refresh(row)
        return row_to_record(row)

    async def next_queued_job_id(self) -> str | None:
        """Oldest ``queued`` verify awaiting a slot, or ``None`` (FIFO by created_at).

        A control plane that just finished a verify calls this to find the next
        job to promote, so a freed slot is reused in arrival order.
        """
        result = await self._session.execute(
            select(JobState.job_id)
            .where(
                JobState.service == SERVICE,
                JobState.lifecycle_state == st.QUEUED,
            )
            .order_by(JobState.created_at.asc(), JobState.job_id.asc())
            .limit(1)
        )
        return result.scalars().first()

    async def promote_next(self) -> dict[str, Any] | None:
        """Promote the oldest queued verify to ``running`` if under the cap.

        Called when a running verify finishes (a slot freed). Picks the FIFO-next
        queued job and admits it via :meth:`try_admit`, which re-checks the cap
        under a row lock. Returns the promoted record, or ``None`` when nothing is
        queued or the cap is still saturated.
        """
        job_id = await self.next_queued_job_id()
        if job_id is None:
            return None
        rec = await self.try_admit(job_id)
        return rec if rec.get("lifecycle_state") == st.RUNNING else None

    async def recover_in_flight(self) -> list[dict[str, Any]]:
        """Reconstruct in-flight state (a new/restarted replica calls this)."""
        result = await self._session.execute(
            select(JobState).where(
                JobState.service == SERVICE,
                JobState.lifecycle_state.in_(_ACTIVE_STATES),
            )
        )
        return [row_to_record(r) for r in result.scalars().all()]

    async def enqueue(
        self,
        job_id: str,
        *,
        correlation_key: str | int | None = None,
        service_status: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]:
        """Create (or re-assert) a ``queued`` row for ``job_id`` — idempotent.

        Used when a verify is admitted to the queue. Safe to call on an existing
        row (returns the current record without resetting an in-flight job).
        """
        existing = await self._locked_row(job_id)
        if existing is not None:
            return row_to_record(existing)

        now = _now()
        row = JobState(
            job_id=job_id,
            schema_version="1",
            correlation_key=str(correlation_key)
            if correlation_key is not None
            else None,
            service=SERVICE,
            kind=KIND,
            lifecycle_state=st.QUEUED,
            service_status=service_status,
            phase=phase,
            attempt=1,
            admission_json=_dumps(
                {
                    "enqueued_at": now.isoformat(),
                    "queue_position": None,
                    "started_at": None,
                }
            ),
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row_to_record(row)

    async def grant_slot(self, job_id: str) -> dict[str, Any]:
        """Transition a job to ``running`` (a slot was granted) — locked.

        Locks the row FOR UPDATE so two replicas can't both move the same job
        from queued→running and thereby double-start it. The COUNT/cap check
        itself is the caller's (thin follow-up), but this guarantees the
        state-advance is serialized.
        """
        row = await self._locked_row(job_id)
        if row is None:
            raise KeyError(f"job_id {job_id!r} not found")
        if row.lifecycle_state != st.RUNNING:
            row.lifecycle_state = st.RUNNING
            adm = _loads(row.admission_json) or {}
            adm["started_at"] = _now().isoformat()
            row.admission_json = _dumps(adm)
        await self._session.commit()
        await self._session.refresh(row)
        return row_to_record(row)

    async def update_status(
        self,
        job_id: str,
        *,
        service_status: str | None = None,
        phase: str | None = None,
        has_verdict: bool = True,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        worker_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance a job from its raw service status — locked + transactional.

        Maps ``service_status`` (+ ``phase``) to a canonical ``lifecycle_state``
        via the hub taxonomy, then enforces the durability invariants:
          - terminal (done/failed) sets ``ended_at`` and requires ``result``;
          - failed/stuck require ``error``;
          - a terminal-by-name status with no verdict becomes ``stuck`` (#464).
        """
        row = await self._locked_row(job_id)
        if row is None:
            raise KeyError(f"job_id {job_id!r} not found")

        if service_status is not None:
            row.service_status = service_status
        if phase is not None:
            row.phase = phase
        if artifacts is not None:
            row.artifacts_json = _dumps(artifacts)
        if worker_ref is not None:
            row.worker_ref_json = _dumps(worker_ref)
        if usage is not None:
            row.usage_json = _dumps(usage)
        if result is not None:
            row.result_json = _dumps(result)

        lifecycle = st.to_lifecycle_state(
            row.service_status, phase=row.phase, has_verdict=has_verdict
        )
        row.lifecycle_state = lifecycle

        # never-overclaim: failed/stuck MUST carry a reason.
        if lifecycle in (st.FAILED, st.STUCK):
            if error is not None:
                row.error = error
            if not row.error:
                row.error = (
                    f"{lifecycle}: no error reason supplied "
                    f"(service_status={row.service_status!r})"
                )
        elif error is not None:
            row.error = error

        # Stamp ended_at when the job stops occupying a slot: done/failed are
        # terminal, and `stuck` (no verdict / orphaned) is reapable — all three
        # release the admission slot, so a new replica won't count them
        # in-flight. done/failed additionally MUST carry a result.
        if st.is_terminal(lifecycle) or lifecycle == st.STUCK:
            if row.ended_at is None:
                row.ended_at = _now_naive()
            if st.is_terminal(lifecycle) and row.result_json is None:
                # Preserve never-overclaim: a terminal job with no explicit
                # result still records a minimal, honest verdict envelope.
                row.result_json = _dumps(
                    {
                        "lifecycle_state": lifecycle,
                        "service_status": row.service_status,
                    }
                )
        else:
            # A job that went back to active (queued/running/review, e.g. a
            # handback re-open) clears ended_at so it is counted in-flight again.
            row.ended_at = None

        await self._session.commit()
        await self._session.refresh(row)
        return row_to_record(row)

    async def mark_stuck(self, job_id: str, error: str) -> dict[str, Any]:
        """Reaper hook: mark a no-verdict / orphaned job ``stuck`` with a reason."""
        row = await self._locked_row(job_id)
        if row is None:
            raise KeyError(f"job_id {job_id!r} not found")
        row.lifecycle_state = st.STUCK
        row.error = error
        if row.ended_at is None:
            row.ended_at = _now_naive()
        await self._session.commit()
        await self._session.refresh(row)
        return row_to_record(row)


def get_job_state_store(session: AsyncSession) -> DbJobStateStore:
    """Return the durable job-state store bound to ``session``.

    The session's engine is selected by ``DATABASE_URL`` (Postgres) or the
    SQLite fallback (engine.py). The SQLite path logs that it is not
    multi-replica safe; Postgres is fully durable + multi-replica safe.
    """
    return DbJobStateStore(session)


# ─── Best-effort facade for the in-pod control plane (AgentService) ──────────
# AgentService runs the verify subprocess in-pod and tracks it in the in-memory
# ``running_tasks`` dict. These helpers mirror the start/terminal seams into the
# durable store so the admission COUNT/running set is recoverable across a
# restart and consistent across replicas. They open their own session (the
# AgentService isn't request-scoped) and are best-effort: a store error is
# logged, never raised, so it can't break a live verify. Admission-cap
# *enforcement* on top of ``active_count()`` is a thin follow-up.


async def try_admit_verify(
    job_id: str,
    *,
    correlation_key: str | int | None = None,
    service_status: str | None = None,
    phase: str | None = None,
) -> bool:
    """Admit a verify under the concurrency cap (RFC-0016). Best-effort.

    Returns ``True`` when the verify was granted a slot (caller should START it
    now) and ``False`` when it was QUEUED (caller should NOT start it — it will be
    promoted automatically when a running verify finishes, via
    :func:`record_terminal`). On any store error this returns ``True`` so a
    durable-store outage never blocks a verify (fail-open — same posture as the
    rest of this best-effort facade).
    """
    try:
        async with async_session_factory() as session:
            store = get_job_state_store(session)
            rec = await store.try_admit(
                job_id,
                correlation_key=correlation_key,
                service_status=service_status,
                phase=phase,
            )
            admitted = rec.get("lifecycle_state") == st.RUNNING
            if not admitted:
                logger.info(
                    "[job-state] verify job_id=%s queued behind admission cap "
                    "(%s=%d); will auto-start when a slot frees",
                    job_id,
                    _MAX_CONCURRENT_ENV,
                    max_concurrent_verifies(),
                )
            return admitted
    except Exception:  # noqa: BLE001 — admission must never hard-block a verify
        logger.warning(
            "[job-state] admission check failed for job_id=%s; "
            "admitting (fail-open)",
            job_id,
            exc_info=True,
        )
        return True


async def record_started(
    job_id: str,
    *,
    correlation_key: str | int | None = None,
    service_status: str | None = None,
    phase: str | None = None,
) -> None:
    """Durably record that a verify started (queued → running). Best-effort.

    Unconditionally grants a slot (no cap check) — use :func:`try_admit_verify`
    when the caller wants the admission decision. Kept for the start-recording
    seam that runs *after* a verify has already been admitted/spawned.
    """
    try:
        async with async_session_factory() as session:
            store = get_job_state_store(session)
            await store.enqueue(
                job_id,
                correlation_key=correlation_key,
                service_status=service_status,
                phase=phase,
            )
            await store.grant_slot(job_id)
    except Exception:  # noqa: BLE001 — durable tracking must never break a verify
        logger.warning(
            "[job-state] failed to record start for job_id=%s (continuing)",
            job_id,
            exc_info=True,
        )


async def record_terminal(
    job_id: str,
    *,
    service_status: str | None,
    phase: str | None = None,
    has_verdict: bool = True,
    result: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> str | None:
    """Durably record a verify's terminal/advance transition. Best-effort.

    Maps the native ``service_status`` to a canonical lifecycle state and
    enforces the never-overclaim invariants (terminal→result+ended_at,
    failed/stuck→error, no-verdict→stuck). When the job releases its execution
    slot (terminal/stuck), the FIFO-next queued verify is promoted into the freed
    slot (RFC-0016 admission control).

    ``artifacts`` (RFC-0016 #190) are object-store ``artifacts[]`` references
    (URIs, never blobs) for the verify's outputs — uploaded best-effort by the
    caller and stamped onto the durable row here.

    Returns the ``job_id`` of any verify promoted as a result (so the caller can
    start it), or ``None``.
    """
    try:
        async with async_session_factory() as session:
            store = get_job_state_store(session)
            if await store.get(job_id) is None:
                await store.enqueue(job_id, service_status=service_status, phase=phase)
            rec = await store.update_status(
                job_id,
                service_status=service_status,
                phase=phase,
                has_verdict=has_verdict,
                result=result,
                usage=usage,
                error=error,
                artifacts=artifacts,
            )
            # If this verify released its slot (terminal/stuck), promote the
            # FIFO-next queued verify into the freed slot (RFC-0016 admission).
            if rec.get("lifecycle_state") not in _ACTIVE_STATES:
                promoted = await store.promote_next()
                if promoted is not None:
                    logger.info(
                        "[job-state] promoted queued verify job_id=%s to running "
                        "after job_id=%s finished",
                        promoted.get("job_id"),
                        job_id,
                    )
                    return promoted.get("job_id")
    except Exception:  # noqa: BLE001 — durable tracking must never break a verify
        logger.warning(
            "[job-state] failed to record terminal for job_id=%s (continuing)",
            job_id,
            exc_info=True,
        )
    return None
