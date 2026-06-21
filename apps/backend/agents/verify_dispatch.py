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

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
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
    # Mount-relative dir that holds the per-task ``flake.nix`` materialized before
    # dispatch. The verify Job ``nix develop``s THIS dir (not the data root) — the
    # data root is mounted at ``mount`` so spec+project resolve, but the flake is
    # materialized into the project worktree (like the lane path), so the develop
    # ref must point there. Defaults to the project subpath.
    flake_subpath: str | None = None


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
        # The flake lives in the project worktree (materialized before dispatch,
        # like the lane path), so develop THAT dir — not the data root, which has
        # no flake.nix and would fail with "flake.nix does not exist" at /work.
        flake_sub = (
            cfg.flake_subpath
            if cfg.flake_subpath is not None
            else (cfg.project_subpath)
        )
        flake_dir = f"{cfg.mount}/{flake_sub}" if flake_sub else cfg.mount
        inner = (
            f"nix develop path:{flake_dir}#default --command bash -c {_shq(verify_cmd)}"
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


async def dispatch_verify_job(  # noqa: PLR0913 - 3 domain args + injectable seams
    *,
    job_id: str,
    spec_dir: Path,
    project_dir: Path,
    correlation_key: str | int | None = None,
    sandbox: Any = None,
    store: Any = None,
    apply_fn: Any = None,
) -> VerifyDispatch | None:
    """Dispatch a verify Job for ``spec_dir`` and record its durable coordinates.

    Returns the ``VerifyDispatch`` (job/namespace/worker_ref) on success, or
    ``None`` when the Nix-lane sandbox isn't configured **or the apply failed**
    (the caller then falls back to the in-pod path — the split never strands a
    verify on a config/cluster gap). Writes a ``queued`` job-state row with
    ``worker_ref`` set to the Job *before* applying, so the control plane can
    reconcile by polling Postgres and the reaper can find an orphaned dispatch.

    Before applying the Job it materializes the per-task ``flake.nix`` into the
    project worktree (the same seam the nixjob lane uses), so the Job's
    ``nix develop`` finds a flake — without this the Job failed exit=1
    "flake.nix does not exist". A non-nix task degrades to running the verify
    directly on the image (no ``nix develop``) rather than stranding.

    ``sandbox`` / ``store`` / ``apply_fn`` are injectable for tests; in production
    they default to ``nix_runner_from_env()``, the durable store opened on a fresh
    engine bound to the calling loop, and the real ``create_namespaced_job`` apply.
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
    # find an orphan even if the apply is interrupted (the row, not the cluster,
    # is the source of truth — concurrency-conventions.md §3).
    await _record_dispatch(
        job_id,
        correlation_key=correlation_key,
        worker_ref=worker_ref,
        store=store,
    )

    # Materialize the per-task flake into the project worktree BEFORE dispatching,
    # exactly like the nixjob LANE path does (run_pytest_lane_via_nix). The verify
    # Job runs ``nix develop path:.../project#default``; without this step the Job
    # fails exit=1 with "flake.nix does not exist". Best-effort: a missing/non-nix
    # manifest degrades to no flake (the Job's nix_develop is suppressed so it runs
    # the verify directly on the image — honest, never strands the dispatch).
    has_flake = _materialize_verify_flake(spec_dir, project_dir)

    # Build the verify-orchestration manifest from the sandbox coordinates and
    # apply it. The manifest carries the dedicated SA + JOB_ID/CORRELATION_KEY/
    # DATABASE_URL env the Job needs to write its own terminal row.
    spec_subpath = _pvc_subpath(spec_dir, sandbox)
    project_subpath = _pvc_subpath(project_dir, sandbox)
    cfg = VerifyJobConfig(
        job_id=job_id,
        image=getattr(sandbox, "image", ""),
        spec_subpath=spec_subpath,
        project_subpath=project_subpath,
        repo_pvc=getattr(sandbox, "repo_pvc", None),
        namespace=namespace,
        nix_store_pvc=getattr(sandbox, "nix_store_pvc", None),
        correlation_key=correlation_key,
        # The flake was materialized into the project worktree, so develop there.
        # When there's no nix manifest, run the verify directly (no nix develop) so
        # a non-nix task isn't stranded on a missing flake.
        flake_subpath=project_subpath,
        nix_develop=has_flake,
    )
    manifest = build_verify_job_manifest(cfg)
    _log.info(
        "[verify-dispatch] dispatching verify Job %s (spec=%s project=%s)",
        name,
        spec_subpath,
        project_subpath,
    )
    try:
        await _apply_verify_job(manifest, namespace, apply_fn=apply_fn)
    except Exception:  # noqa: BLE001 — apply gap must not strand: fall back in-pod
        _log.warning(
            "[verify-dispatch] apply of verify Job %s failed; caller should fall "
            "back to in-pod (the queued row will advance with the same job_id)",
            name,
            exc_info=True,
        )
        return None

    return VerifyDispatch(
        job_id=job_id, job_name=name, namespace=namespace, worker_ref=worker_ref
    )


async def _apply_verify_job(
    manifest: dict[str, Any], namespace: str, *, apply_fn: Any = None
) -> None:
    """Fire-and-forget create the verify Job (reconcile-by-poll owns the rest).

    Unlike the synchronous sandbox lane (``KubeJobSandbox.run`` applies, watches,
    then deletes), the verify Job is created and left to run: it writes its own
    terminal job-state row and is GC'd by ``ttlSecondsAfterFinished``. The control
    plane reconciles + reaps by polling Postgres, so no watch loop is held here.

    ``apply_fn(namespace, manifest)`` is injectable for tests; production loads
    the in-cluster (or kubeconfig) client lazily and calls ``create_namespaced_job``.
    """
    if apply_fn is not None:
        await apply_fn(namespace, manifest)
        return
    api, batch = await _k8s_batch()
    try:
        await batch.create_namespaced_job(namespace, manifest)
    finally:
        await api.close()


async def _k8s_batch() -> tuple[Any, Any]:
    """Load kube config (in-cluster, kubeconfig fallback) and return ``(api, batch)``.

    Isolates the untyped ``kubernetes_asyncio`` API behind a single ``Any`` seam so
    mypy --strict stays clean whether or not the (stub-less) package is installed,
    and the lazy import keeps the backend importable without a cluster.
    """
    k8s: Any = _import_kubernetes_asyncio()
    client, config = k8s.client, k8s.config
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001 - dev/test fallback
        await config.load_kube_config()
    api = client.ApiClient()
    return api, client.BatchV1Api(api)


def _import_kubernetes_asyncio() -> Any:
    """Lazily import the (untyped, stub-less) ``kubernetes_asyncio`` package."""
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("kubernetes_asyncio")


def _pvc_subpath(path: Path, sandbox: Any) -> str:
    """PVC-relative subpath for ``path`` under the sandbox data root, or ''."""
    from tools.runners.kube_sandbox import pvc_subpath  # noqa: PLC0415 - lazy by design

    data_root = getattr(sandbox, "data_root", "/home/nonroot/.tfactory")
    sub = pvc_subpath(str(path), data_root)
    return sub or ""


def _materialize_verify_flake(spec_dir: Path, project_dir: Path) -> bool:
    """Write the per-task ``flake.nix`` into ``project_dir`` (the lane-path seam).

    Reuses ``agents.nix_env.materialize_flake`` (which renders the contract's
    RFC-0005 ``environment`` into a generated flake, respecting a repo-owned one)
    so the verify-orchestration Job has the same toolchain the build + lane paths
    do — no drift, no reinvention. Returns ``True`` when a flake is present at
    ``project_dir`` after this call (so the Job should ``nix develop`` it), or
    ``False`` when there is no nix manifest (the caller then runs the verify
    directly on the image). Best-effort: any error degrades to ``False`` rather
    than stranding the dispatch.
    """
    try:
        from agents.nix_env import (  # noqa: PLC0415 - lazy by design
            environment_from_contract,
            materialize_flake,
        )

        env = environment_from_contract(spec_dir)
        plan = materialize_flake(spec_dir, project_dir, env=env)
        if plan is not None:
            _log.info(
                "[verify-dispatch] materialized flake.nix into %s for verify Job",
                project_dir,
            )
            return True
        # No nix manifest, but a repo may still carry a hand-written flake.nix.
        if (Path(project_dir) / "flake.nix").exists():
            return True
        _log.info(
            "[verify-dispatch] no nix environment for %s; verify Job will run "
            "the pipeline directly on the image (no nix develop)",
            spec_dir.name,
        )
        return False
    except Exception:  # noqa: BLE001 — flake gap must not strand the dispatch
        _log.warning(
            "[verify-dispatch] flake materialization failed for %s; verify Job "
            "will run directly on the image",
            spec_dir.name,
            exc_info=True,
        )
        return (Path(project_dir) / "flake.nix").exists()


@asynccontextmanager
async def _store_for(store: Any) -> AsyncIterator[tuple[Any, bool]]:
    """Yield a durable job-state store + whether we own its session.

    When the caller injects a ``store`` (tests, or a request-scoped store) we use
    it and own nothing. Otherwise we open the web-server durable store on a
    **fresh engine bound to the current running loop** (the control plane / reaper
    isn't request-scoped). The web-server package is a sibling app not on the
    backend's import path at type-check time, so the import is lazy + ignored for
    mypy; at runtime it resolves in the pod.

    Why a fresh engine and not the process-global ``async_session_factory``:
    asyncpg (and aiosqlite) bind a connection to the loop that created it, so a
    pooled connection from the app's main-loop engine raises ``RuntimeError: got
    Future attached to a different loop`` when reused from the **blocking dispatch
    path**, which runs on its own private loop in a worker thread (see
    ``gen_functional._run_dispatch_blocking``). Creating the engine here means its
    connections are always opened on the loop that uses them. This mirrors how
    PFactory's durable store keeps DB I/O on a single, owned loop (PFactory #220).
    The owned engine is disposed when the context exits so no connection leaks.
    """
    if store is not None:
        yield store, False
        return
    from server.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
        job_state_store as jss,
    )

    engine, factory = _fresh_store_engine()
    try:
        async with factory() as session:
            yield jss.get_job_state_store(session), True
    finally:
        await engine.dispose()


def _fresh_store_engine() -> tuple[Any, Any]:
    """Build a throwaway async engine + sessionmaker bound to the current loop.

    Reuses the web-server's resolved ``DATABASE_URL`` + driver connect-args so the
    fresh engine targets the same Postgres (or SQLite-dev fallback) the app uses —
    only the *loop affinity* differs. asyncpg/aiosqlite connections are created
    lazily on first use, i.e. on whichever loop drives the session, so opening the
    engine on the dispatch's private loop keeps every Future on that one loop.

    The SQLAlchemy async API is reached through a single ``Any`` seam
    (:func:`_import_sqlalchemy_async`) — the same shape as
    :func:`_import_kubernetes_asyncio` — so mypy --strict stays clean whether or
    not the SQLAlchemy stubs are installed in the lint env (the ratchet installs
    deps best-effort), and ``warn_unused_ignores`` never trips either way.
    """
    eng: Any = _import_engine_module()
    sa_async: Any = _import_sqlalchemy_async()

    # _resolve_database_url / _connect_args_for are module-level helpers in
    # engine.py (not class members); reuse them so the fresh engine matches the
    # app's URL + driver args exactly.
    url = eng._resolve_database_url()
    engine = sa_async.create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=eng._connect_args_for(url),
    )
    factory = sa_async.async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def _import_engine_module() -> Any:
    """Lazily import the web-server's DB engine module behind an ``Any`` seam.

    The web-server (``server.*``) is a sibling app not on the backend's import
    path at type-check time; the dynamic import keeps mypy from resolving it (and
    so from needing an ``# type: ignore`` that would be unused where it *is*
    resolvable). Resolves at runtime in the pod.
    """
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("server.database.engine")


def _import_sqlalchemy_async() -> Any:
    """Lazily import ``sqlalchemy.ext.asyncio`` behind an ``Any`` seam.

    Mirrors :func:`_import_kubernetes_asyncio`: a dynamic import so mypy --strict
    is clean regardless of whether the SQLAlchemy stubs are present in the lint
    env, with no static ``import-not-found`` and no unused-ignore.
    """
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("sqlalchemy.ext.asyncio")


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


# ── Control-plane reconcile + reap loop (wired into the app lifespan) ──────────
#
# Mirrors AIFactory build_backend's kubejob reconcile loop (RFC-0016 #671): when
# verifies run as k8s Jobs, the control plane polls Postgres for terminal
# transitions the Jobs wrote (so a missed completion event never strands a
# verify) and reaps vanished / deadline-exceeded Jobs on an interval. The loop is
# started from the web-server lifespan only when verify_exec_mode() == kubejob.


def _is_k8s_job_ref(record: dict[str, Any]) -> bool:
    """True when a durable row points at a dispatched verify k8s Job."""
    ref = record.get("worker_ref") or {}
    return isinstance(ref, dict) and ref.get("kind") == "k8s-job"


async def _probe_job(
    namespace: str, job_name: str, *, probe_fn: Any = None
) -> tuple[bool, bool]:
    """Return ``(job_exists, job_active)`` for the named Job. Fail-safe.

    Defaults to a lazy in-cluster ``read_namespaced_job`` probe; injectable for
    tests. On any probe error the Job is reported ``(exists=True, active=True)``
    so a transient API blip never makes the reaper reap a live verify.
    """
    if probe_fn is not None:
        result: tuple[bool, bool] = await probe_fn(namespace, job_name)
        return result
    try:
        api, batch = await _k8s_batch()
        try:
            job = await batch.read_namespaced_job(job_name, namespace)
        finally:
            await api.close()
    except Exception:  # noqa: BLE001 - a probe gap must not reap a live verify
        _log.debug(
            "[verify-dispatch] job probe failed for %s/%s (treating as active)",
            namespace,
            job_name,
            exc_info=True,
        )
        return True, True
    st = getattr(job, "status", None)
    active = bool(getattr(st, "active", 0)) if st is not None else False
    return True, active


async def reconcile_and_reap_once(*, store: Any = None, probe_fn: Any = None) -> int:
    """One reconcile + reap pass over active verify k8s-Job rows. Never raises.

    Lists the durable active (queued/running) verify rows, and for each one that
    points at a dispatched k8s Job: reconciles (a terminal row the Job wrote is
    left as-is) and reaps an orphan (Job vanished / finished with no verdict).
    Returns the number of rows reaped ``stuck`` (for observability / tests).
    """
    reaped = 0
    try:
        async with _store_for(store) as (s, _owned):
            rows = await s.recover_in_flight()
            for rec in rows:
                if not _is_k8s_job_ref(rec):
                    continue
                job_id = rec.get("job_id")
                if not job_id:
                    continue
                ref = rec.get("worker_ref") or {}
                namespace = ref.get("namespace") or "factory"
                job_name = ref.get("job_name") or verify_job_name(job_id)
                # Reconcile first: a terminal row the Job already wrote wins.
                if is_terminal_record(await reconcile_verify_job(job_id, store=s)):
                    continue
                exists, active = await _probe_job(
                    namespace, job_name, probe_fn=probe_fn
                )
                reaped_rec = await reap_if_orphaned(
                    job_id, job_exists=exists, job_active=active, store=s
                )
                if reaped_rec is not None:
                    reaped += 1
    except Exception:  # noqa: BLE001 - a bad tick must not crash the loop
        _log.warning("[verify-dispatch] reconcile/reap tick failed", exc_info=True)
    return reaped


async def reconcile_and_reap_loop(
    *, stop: asyncio.Event, interval_seconds: float = 15.0, probe_fn: Any = None
) -> None:
    """Periodic reconcile-by-poll + reaper for k8s-Job verifies (mirrors #671).

    Started from the web-server lifespan only when verify_exec_mode() == kubejob.
    Each tick reconciles terminal transitions the Jobs wrote and reaps vanished
    Jobs, so a missed completion event never strands a verify. Never raises — a
    bad tick is logged and the loop continues.
    """
    _log.info(
        "[verify-dispatch] reconcile loop started (interval=%.0fs)", interval_seconds
    )
    while not stop.is_set():
        await reconcile_and_reap_once(probe_fn=probe_fn)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
    _log.info("[verify-dispatch] reconcile loop stopped")
