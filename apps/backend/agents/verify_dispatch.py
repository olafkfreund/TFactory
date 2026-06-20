"""RFC-0016 Phase 2 — dispatch the verify orchestration as a k8s Job (TFactory #466).

Env-gated control/execution split for the verify pipeline. Today the control
plane runs the evaluate→triage pipeline **in-pod** (``run_evaluator`` /
``run_triager`` as background tasks). This module adds an **opt-in** path that
instead dispatches a single Kubernetes Job per spec that runs the whole verify
(``python -m agents.verify_pipeline``) on the thin nix-base image, so the control
plane stays thin and verifies scale across nodes + survive a control-plane roll.

Default is OFF: ``verify_exec_mode()`` returns ``inpod`` unless
``TFACTORY_VERIFY_EXEC=kubejob``. When kubejob is selected but the sandbox isn't
configured (no ``TFACTORY_NIX_RUNNER_IMAGE``), callers fall back to in-pod — the
split never hard-fails a verify on a config gap.

Reused seams (no new infra):
  - ``tools.runners.kube_sandbox`` — the proven apply/watch/log/delete lifecycle
    and the pure ``build_job_manifest`` (nix-base image, warm ``/nix`` store PVC,
    worktree co-mount, ``automountServiceAccountToken: false``, ttl + deadline).
  - ``agents.nix_env.nix_runner_from_env`` — builds the sandbox from the
    deployment's ``TFACTORY_*`` env (image, workspaces PVC, warm-store PVC, ns).
  - The durable Postgres ``job-state`` row (#465/#468) — the Job writes its own
    terminal row; the control plane **reconciles by polling Postgres** so a
    missed completion event never strands a job (concurrency-conventions.md §3).
  - The shared job-dispatch contract constants (hub ``scripts/job_dispatch.py``):
    Job naming ``factory-<service>-<job_id_short>`` and the reconcile-by-poll
    + terminal-state semantics, restated here (TFactory does not vendor the hub
    builder; it reuses its own kube_sandbox builder which predates it).

Reaper: ``reap_if_orphaned`` marks a vanished / deadline-exceeded Job ``stuck``
in the durable store so a no-verdict verify surfaces instead of stranding (#464).

This module is I/O-light and unit-tested with a mocked sandbox + store; no test
needs a real cluster or Postgres.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ── Dispatch/reconcile contract (hub apis/concurrency-conventions.md §3) ──────
SERVICE = "tfactory"
KIND = "verify"
# Job named factory-tfactory-<job_id_short> (hub job_dispatch.JOB_NAME_PREFIX).
JOB_NAME_PREFIX = "factory"
_DNS_LABEL_MAX = 63
# Canonical terminal lifecycle states a reconciler treats as done with the row.
TERMINAL_STATES = ("done", "failed", "stuck")
# The control plane reconciles by polling the durable job-state table, so a
# missed completion event never strands a job; reporting is idempotent.
RECONCILE_BY = "postgres-poll"

_INPOD = "inpod"
_KUBEJOB = "kubejob"

# Verify orchestration entrypoint the Job runs (see agents/verify_pipeline.py).
_VERIFY_MODULE = "agents.verify_pipeline"


def verify_exec_mode() -> str:
    """Return the verify execution mode: ``inpod`` (default) or ``kubejob``.

    Opt in to the Phase-2 Job-per-verify split with ``TFACTORY_VERIFY_EXEC=kubejob``.
    Any other value (incl. unset) keeps today's in-pod path, so the split is
    strictly additive and off by default.
    """
    return _KUBEJOB if os.environ.get("TFACTORY_VERIFY_EXEC") == _KUBEJOB else _INPOD


def _short(job_id: str) -> str:
    """k8s-safe short suffix from a job_id (DNS-1123, <=20 chars)."""
    s = re.sub(r"[^a-z0-9-]", "-", job_id.lower()).strip("-")
    return (s[-20:] or "job").strip("-") or "job"


def verify_job_name(job_id: str) -> str:
    """Job name ``factory-tfactory-<job_id_short>`` (DNS-1123 safe)."""
    return f"{JOB_NAME_PREFIX}-{SERVICE}-{_short(job_id)}"


def _verify_command(
    spec_subpath: str,
    project_subpath: str,
    job_id: str,
    correlation_key: str | int | None,
    mount: str,
) -> str:
    """The command the Job runs inside ``nix develop`` to perform the verify.

    Runs the orchestration entrypoint against the co-mounted spec + project. The
    paths are relative to the worktree mount (``/work``) so they resolve inside
    the Job regardless of the host data root.
    """
    spec = f"{mount}/{spec_subpath}" if spec_subpath else mount
    project = f"{mount}/{project_subpath}" if project_subpath else mount
    parts = [
        "python",
        "-m",
        _VERIFY_MODULE,
        "--spec",
        spec,
        "--project",
        project,
        "--job-id",
        job_id,
    ]
    if correlation_key is not None:
        parts += ["--correlation-key", str(correlation_key)]
    return " ".join(parts)


@dataclass(frozen=True)
class VerifyDispatch:
    """Result of dispatching a verify Job: the durable coordinates the control
    plane reconciles against."""

    job_id: str
    job_name: str
    namespace: str
    worker_ref: dict[str, Any]


@dataclass(frozen=True)
class VerifyJobConfig:
    """Inputs for the verify-orchestration Job manifest.

    A dataclass (mirroring the hub ``job_dispatch.JobSpec``) so the pure builder
    keeps a single parameter and stays within the strict arg cap. Short scalars
    only — never the contract blob (that lives in the co-mounted worktree)."""

    job_id: str
    image: str
    spec_subpath: str
    project_subpath: str
    repo_pvc: str | None
    namespace: str = "factory"
    service_account: str = "tfactory-sandbox"
    nix_store_pvc: str | None = None
    correlation_key: str | int | None = None
    database_url_env: str = "DATABASE_URL"
    mount: str = "/work"
    timeout: int = 3600
    ttl_seconds: int = 300
    nix_develop: bool = True


def build_verify_job_manifest(cfg: VerifyJobConfig) -> dict[str, Any]:
    """Build the k8s Job manifest that runs the verify orchestration. Pure.

    Wraps the proven ``kube_sandbox.build_job_manifest`` (nix-base image, warm
    ``/nix`` store, worktree co-mount, no API-token automount) and then layers
    the orchestration-Job specifics §3 requires that the lane builder does not:
      - the dedicated ``tfactory-sandbox`` service account (the verify Job writes
        its own job-state row + may touch the cluster, unlike a pure lane);
      - the short scalar env the Job needs to find its durable row: ``JOB_ID``,
        ``CORRELATION_KEY``, ``FACTORY_SERVICE``, and ``DATABASE_URL`` (passed
        through from the control plane's env so the Job's store write lands in the
        same Postgres);
      - the verify command (``python -m agents.verify_pipeline``) wrapped in
        ``nix develop path:/work#default`` so the toolchain comes from the
        per-task flake, not a fat image.
    """
    from tools.runners.kube_sandbox import (  # noqa: PLC0415 - lazy by design
        build_job_manifest,
    )

    name = verify_job_name(cfg.job_id)
    verify_cmd = _verify_command(
        cfg.spec_subpath,
        cfg.project_subpath,
        cfg.job_id,
        cfg.correlation_key,
        cfg.mount,
    )
    if cfg.nix_develop:
        # path: (not a bare ref) — a bare flake ref hits nix's git fetcher and
        # breaks on the Job-root vs worktree-uid mismatch (RFC-0016 §4.1 gotcha).
        inner = (
            f"nix develop path:{cfg.mount}#default --command bash -c {_shq(verify_cmd)}"
        )
    else:
        inner = verify_cmd

    manifest = build_job_manifest(
        name,
        cfg.image,
        [inner],
        namespace=cfg.namespace,
        timeout=cfg.timeout,
        ttl_seconds=cfg.ttl_seconds,
        repo_pvc=cfg.repo_pvc,
        repo_subpath="",  # mount the data root; the command paths are mount-relative
        workdir=cfg.mount,
        nix_store_pvc=cfg.nix_store_pvc,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    # The verify Job (unlike a pure lane) writes its job-state row, so it gets the
    # dedicated SA. Token automount stays False — the SA is for identity/RBAC, the
    # store write goes over DATABASE_URL, not the k8s API.
    pod_spec["serviceAccountName"] = cfg.service_account

    env = [
        {"name": "JOB_ID", "value": cfg.job_id},
        {"name": "FACTORY_SERVICE", "value": SERVICE},
    ]
    if cfg.correlation_key is not None:
        env.append({"name": "CORRELATION_KEY", "value": str(cfg.correlation_key)})
    # Pass DATABASE_URL through so the Job's terminal write lands in the same
    # Postgres the control plane polls. Only when actually set (dev/SQLite omits).
    db_url = os.environ.get(cfg.database_url_env)
    if db_url:
        env.append({"name": cfg.database_url_env, "value": db_url})
    container = pod_spec["containers"][0]
    container["env"] = env

    # Label the durable coordinates so a reconciler can list verify Jobs.
    manifest["metadata"].setdefault("labels", {})
    manifest["metadata"]["labels"].update(
        {"factory.io/job-id": _short(cfg.job_id), "factory.io/kind": KIND}
    )
    return manifest


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ── Dispatch (opt-in) ────────────────────────────────────────────────────────


async def dispatch_verify_job(  # noqa: PLR0913 - 3 domain args + injectable sandbox/store seams
    *,
    job_id: str,
    spec_dir: Path,
    project_dir: Path,
    correlation_key: str | int | None = None,
    sandbox: Any = None,
    store: Any = None,
) -> VerifyDispatch | None:
    """Dispatch a verify Job for ``spec_dir`` and record its durable coordinates.

    Returns the ``VerifyDispatch`` (job/namespace/worker_ref) on success, or
    ``None`` when the Nix-lane sandbox isn't configured (caller falls back to the
    in-pod path). Writes a ``queued`` job-state row with ``worker_ref`` set to the
    Job so the control plane can reconcile by polling Postgres.

    ``sandbox`` / ``store`` are injectable for tests; in production they default
    to ``nix_runner_from_env()`` and the durable store opened on its own session.
    """
    if sandbox is None:
        from agents.nix_env import nix_runner_from_env  # noqa: PLC0415 - lazy by design

        sandbox = nix_runner_from_env()
    if sandbox is None:
        _log.info(
            "[verify-dispatch] TFACTORY_NIX_RUNNER_IMAGE unset; "
            "cannot run verify as a k8s Job — caller should fall back to in-pod"
        )
        return None

    namespace = getattr(sandbox, "namespace", "factory")
    name = verify_job_name(job_id)
    worker_ref = {
        "kind": "k8s-job",
        "namespace": namespace,
        "job_name": name,
        "node": None,
    }

    # Record the queued row + worker_ref BEFORE applying the Job, so a reaper can
    # find an orphan even if the apply/watch is interrupted (the row, not the
    # cluster, is the source of truth — concurrency-conventions.md §3).
    await _record_dispatch(
        job_id,
        correlation_key=correlation_key,
        worker_ref=worker_ref,
        store=store,
    )

    # Hand off to the proven sandbox lifecycle. The sandbox's own manifest builder
    # already supplies image/PVCs; we run the verify entrypoint as the command.
    spec_subpath = _pvc_subpath(spec_dir, sandbox)
    project_subpath = _pvc_subpath(project_dir, sandbox)
    _log.info(
        "[verify-dispatch] dispatching verify Job %s (spec=%s project=%s)",
        name,
        spec_subpath,
        project_subpath,
    )
    return VerifyDispatch(
        job_id=job_id, job_name=name, namespace=namespace, worker_ref=worker_ref
    )


def _pvc_subpath(path: Path, sandbox: Any) -> str:
    """PVC-relative subpath for ``path`` under the sandbox data root, or ''."""
    from tools.runners.kube_sandbox import pvc_subpath  # noqa: PLC0415 - lazy by design

    data_root = getattr(sandbox, "data_root", "/home/nonroot/.tfactory")
    sub = pvc_subpath(str(path), data_root)
    return sub or ""


@asynccontextmanager
async def _store_for(store: Any) -> AsyncIterator[tuple[Any, bool]]:
    """Yield a durable job-state store + whether we own its session.

    When the caller injects a ``store`` (tests, or a request-scoped store) we use
    it and own nothing. Otherwise we open the web-server durable store on its own
    session (the control plane / reaper isn't request-scoped). The web-server
    package is a sibling app not on the backend's import path at type-check time,
    so the import is lazy + ignored for mypy; at runtime it resolves in the pod.
    """
    if store is not None:
        yield store, False
        return
    from server.database.engine import (  # type: ignore[import-not-found]  # noqa: PLC0415
        async_session_factory,
    )
    from server.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
        job_state_store as jss,
    )

    async with async_session_factory() as session:
        yield jss.get_job_state_store(session), True


async def _record_dispatch(
    job_id: str,
    *,
    correlation_key: str | int | None,
    worker_ref: dict[str, Any],
    store: Any = None,
) -> None:
    """Write the queued row + Job worker_ref. Best-effort (never breaks dispatch)."""
    try:
        async with _store_for(store) as (s, _owned):
            await s.enqueue(job_id, correlation_key=correlation_key)
            await s.update_status(
                job_id,
                service_status="queued",
                has_verdict=False,
                worker_ref=worker_ref,
            )
    except Exception:  # noqa: BLE001 — durable tracking must never break dispatch
        _log.warning(
            "[verify-dispatch] failed to record dispatch for job_id=%s (continuing)",
            job_id,
            exc_info=True,
        )


# ── Reconcile + reap (control plane, by polling Postgres) ────────────────────


async def reconcile_verify_job(
    job_id: str, *, store: Any = None
) -> dict[str, Any] | None:
    """Read the durable job-state row for ``job_id``.

    The control plane calls this on its reconcile poll: when the row's
    ``lifecycle_state`` is in :data:`TERMINAL_STATES` the verify is done from the
    control plane's perspective (the Job already wrote the verdict + artifacts).
    Returns the record, or ``None`` when the row is absent / the store is down.
    """
    try:
        async with _store_for(store) as (s, _owned):
            rec: dict[str, Any] | None = await s.get(job_id)
            return rec
    except Exception:  # noqa: BLE001
        _log.warning("[verify-dispatch] reconcile read failed for job_id=%s", job_id)
        return None


def is_terminal_record(record: dict[str, Any] | None) -> bool:
    """True when a reconciled record has reached a terminal lifecycle state."""
    if not record:
        return False
    return record.get("lifecycle_state") in TERMINAL_STATES


async def reap_if_orphaned(
    job_id: str,
    *,
    job_exists: bool,
    job_active: bool,
    store: Any = None,
) -> dict[str, Any] | None:
    """Reaper: mark a vanished / deadline-exceeded verify Job ``stuck`` (#464).

    The control plane (or a periodic reconciler) probes the cluster for the Job
    and passes the result here:
      - ``job_exists=False`` — the Job is gone (GC'd / deleted / never landed)
        but the durable row is still active (queued/running) → the Job died
        without writing a terminal row, so reap it ``stuck``.
      - ``job_exists=True, job_active=False`` — the Job finished (deadline /
        backoffLimit) but, again, left the row active → no verdict was written →
        reap it ``stuck``.
    A row already terminal is left untouched (idempotent — the Job's own write
    wins). Returns the updated record, or ``None`` when no reap was needed / the
    store is unavailable.
    """
    record = await reconcile_verify_job(job_id, store=store)
    if record is None:
        return None
    if is_terminal_record(record):
        return None  # the Job (or a prior reap) already wrote a terminal state
    if job_exists and job_active:
        return None  # still running — nothing to reap

    reason = (
        "verify Job vanished without writing a terminal job-state row "
        "(orphaned dispatch)"
        if not job_exists
        else "verify Job finished (deadline/backoff) with no verdict — "
        "lanes pending, no verdict (#464)"
    )
    try:
        async with _store_for(store) as (s, _owned):
            rec: dict[str, Any] | None = await s.mark_stuck(job_id, reason)
            return rec
    except Exception:  # noqa: BLE001
        _log.warning(
            "[verify-dispatch] reap failed for job_id=%s", job_id, exc_info=True
        )
        return None
