#!/usr/bin/env python3
"""Tests for the env-gated verify control/execution split (RFC-0016, TFactory #466).

Covers:
- ``verify_exec_mode`` selects in-pod (default / any other value) vs kubejob
  (``TFACTORY_VERIFY_EXEC=kubejob``).
- ``build_verify_job_manifest`` produces a correct Job: nix-base image, the
  ``python -m agents.verify_pipeline`` command wrapped in ``nix develop``, the
  worktree + warm-store mounts, the ``tfactory-sandbox`` SA, no token automount,
  and the JOB_ID / CORRELATION_KEY / FACTORY_SERVICE env.
- ``verify_job_name`` is DNS-1123 safe and prefixed ``factory-tfactory-``.
- ``dispatch_verify_job`` returns None (fall back to in-pod) when the sandbox is
  unconfigured, and records a queued row + k8s-job worker_ref when it is.
- ``reconcile_verify_job`` / ``is_terminal_record`` mark terminal from the
  durable row (the control plane reconciles by polling Postgres).
- ``reap_if_orphaned`` marks a vanished or deadline-exceeded Job ``stuck`` (#464)
  and leaves an already-terminal / still-running row untouched.

The durable store is the REAL ``DbJobStateStore`` on in-memory async SQLite (no
Postgres / no cluster), injected via the ``store=`` seam.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from agents import verify_dispatch as vd
from agents.verify_dispatch import (
    VerifyJobConfig,
    build_verify_job_manifest,
    is_terminal_record,
    verify_exec_mode,
    verify_job_name,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.database.models import Base  # noqa: E402
from server.services.job_state_store import (  # noqa: E402
    DbJobStateStore,
    get_job_state_store,
)

_IMAGE = "ghcr.io/olafkfreund/tfactory-runner-nix:latest"


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield get_job_state_store(s)
    await engine.dispose()


class _FakeSandbox:
    """Stand-in for ``KubeJobSandbox`` — carries the coordinates dispatch reads."""

    def __init__(self, namespace="factory", data_root="/home/nonroot/.tfactory"):
        self.namespace = namespace
        self.data_root = data_root
        self.image = _IMAGE
        self.repo_pvc = "tfactory-data"
        self.nix_store_pvc = "tfactory-nix-store"


class _RecordingApply:
    """Injectable ``apply_fn`` that records the manifest it was asked to create."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def __call__(self, namespace: str, manifest: dict) -> None:
        self.calls.append((namespace, manifest))
        if self.fail:
            raise RuntimeError("simulated k8s apply failure")


# ─── verify_exec_mode (env selects in-pod vs kubejob) ─────────────────────────


def test_verify_exec_mode_defaults_inpod(monkeypatch):
    monkeypatch.delenv("TFACTORY_VERIFY_EXEC", raising=False)
    assert verify_exec_mode() == "inpod"


def test_verify_exec_mode_kubejob_opt_in(monkeypatch):
    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "kubejob")
    assert verify_exec_mode() == "kubejob"


def test_verify_exec_mode_unknown_value_is_inpod(monkeypatch):
    # Any value other than the exact "kubejob" keeps the safe default.
    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "docker")
    assert verify_exec_mode() == "inpod"


# ─── verify_job_name (DNS-1123 safe, prefixed) ────────────────────────────────


def test_verify_job_name_prefix_and_dns_safe():
    name = verify_job_name("proj-abc:042-verify")
    assert name.startswith("factory-tfactory-")
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9-]+", name) is not None


def test_verify_job_name_sanitizes_and_truncates():
    name = verify_job_name("A_VERY/LONG::Job__Id::With::Junk::1234567890")
    assert name.startswith("factory-tfactory-")
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9-]+", name) is not None


# ─── build_verify_job_manifest (Job manifest correctness) ─────────────────────


def _cfg(**kw) -> VerifyJobConfig:
    base = {
        "job_id": "proj-abc:042-verify",
        "image": _IMAGE,
        "spec_subpath": "workspaces/proj/.tfactory/specs/042",
        "project_subpath": "workspaces/proj",
        "repo_pvc": "tfactory-data",
        "nix_store_pvc": "tfactory-nix-store",
        "correlation_key": 482,
    }
    base.update(kw)
    return VerifyJobConfig(**base)  # type: ignore[arg-type]


def test_manifest_is_a_job_with_nix_base_image():
    m = build_verify_job_manifest(_cfg())
    assert m["kind"] == "Job"
    assert m["spec"]["backoffLimit"] == 0  # no silent retries
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == _IMAGE


def test_manifest_runs_verify_pipeline_via_nix_develop():
    m = build_verify_job_manifest(_cfg())
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    # nix develop targets the PROJECT worktree where the flake is materialized
    # (the data root at /work has no flake.nix — that was BUG 2).
    assert "nix develop path:/work/workspaces/proj#default" in cmd
    assert "python -m agents.verify_pipeline" in cmd
    assert "--spec /work/workspaces/proj/.tfactory/specs/042" in cmd
    assert "--project /work/workspaces/proj" in cmd
    assert "--job-id proj-abc:042-verify" in cmd
    assert "--correlation-key 482" in cmd


def test_manifest_nix_develop_targets_explicit_flake_subpath():
    # When the flake lives in a different dir than the project, develop that dir.
    m = build_verify_job_manifest(_cfg(flake_subpath="workspaces/proj/env"))
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop path:/work/workspaces/proj/env#default" in cmd


def test_manifest_without_nix_develop_runs_verify_directly():
    # A non-nix task (no flake) must run the verify directly on the image, not
    # nix develop a nonexistent flake.
    m = build_verify_job_manifest(_cfg(nix_develop=False))
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop" not in cmd
    assert "python -m agents.verify_pipeline" in cmd


def test_manifest_uses_tfactory_sandbox_sa_no_token_automount():
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    assert ps["serviceAccountName"] == "tfactory-sandbox"
    assert ps["automountServiceAccountToken"] is False


def test_manifest_mounts_worktree_and_warm_nix_store():
    m = build_verify_job_manifest(_cfg())
    ps = m["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in ps["volumes"]}
    assert vols["repo"]["persistentVolumeClaim"]["claimName"] == "tfactory-data"
    assert (
        vols["nix-store"]["persistentVolumeClaim"]["claimName"] == "tfactory-nix-store"
    )
    mounts = {vm["name"]: vm for vm in ps["containers"][0]["volumeMounts"]}
    assert mounts["repo"]["mountPath"] == "/work"
    assert mounts["nix-store"]["mountPath"] == "/nix"


def test_manifest_carries_job_state_env():
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["JOB_ID"] == "proj-abc:042-verify"
    assert env["FACTORY_SERVICE"] == "tfactory"
    assert env["CORRELATION_KEY"] == "482"


def test_manifest_passes_database_url_through_when_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://h/db")
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DATABASE_URL"] == "postgresql+asyncpg://h/db"


def test_manifest_omits_database_url_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    assert all(e["name"] != "DATABASE_URL" for e in c["env"])


def test_manifest_labels_durable_coordinates():
    labels = build_verify_job_manifest(_cfg())["metadata"]["labels"]
    assert labels["factory.io/kind"] == "verify"
    assert "factory.io/job-id" in labels


# ─── dispatch_verify_job (fall back vs record) ────────────────────────────────


async def test_dispatch_falls_back_when_sandbox_unconfigured(monkeypatch, store):
    # No TFACTORY_NIX_RUNNER_IMAGE → nix_runner_from_env() is None → None.
    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    result = await vd.dispatch_verify_job(
        job_id="j1",
        spec_dir=Path("/home/nonroot/.tfactory/ws/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws"),
        store=store,
    )
    assert result is None
    # No row was created (we never got far enough to record).
    assert await store.get("j1") is None


async def test_dispatch_records_queued_row_with_k8s_worker_ref(store):
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:042-verify",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        correlation_key=99,
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    assert result.job_name == verify_job_name("proj:042-verify")
    assert result.job_name.startswith("factory-tfactory-")
    assert result.worker_ref["kind"] == "k8s-job"
    assert result.worker_ref["job_name"] == result.job_name

    rec = await store.get("proj:042-verify")
    assert rec is not None
    assert rec["lifecycle_state"] == "queued"
    assert rec["worker_ref"]["kind"] == "k8s-job"
    assert rec["correlation_key"] == "99"


async def test_dispatch_applies_the_verify_job_manifest(store):
    # The dispatch must actually create the Job (#466: it never did before the
    # wiring fix). The applied manifest is the verify-orchestration Job.
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:042",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    assert len(apply.calls) == 1
    ns, manifest = apply.calls[0]
    assert ns == "factory"
    assert manifest["kind"] == "Job"
    cmd = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "python -m agents.verify_pipeline" in cmd


async def test_dispatch_falls_back_to_inpod_when_apply_fails(store):
    # A cluster/apply gap must NOT strand the verify: dispatch returns None so the
    # caller runs the in-pod path instead.
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply(fail=True)
    result = await vd.dispatch_verify_job(
        job_id="proj:043",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is None  # → caller falls back to in-pod


# ─── reconcile_verify_job + is_terminal_record ────────────────────────────────


async def test_reconcile_marks_terminal_from_job_state(store):
    await store.enqueue("jt")
    await store.grant_slot("jt")
    # The Job wrote its terminal verdict row (done).
    await store.update_status("jt", service_status="triaged", has_verdict=True)

    rec = await vd.reconcile_verify_job("jt", store=store)
    assert rec is not None
    assert rec["lifecycle_state"] == "done"
    assert is_terminal_record(rec) is True


async def test_reconcile_running_is_not_terminal(store):
    await store.enqueue("jr")
    await store.grant_slot("jr")
    rec = await vd.reconcile_verify_job("jr", store=store)
    assert rec["lifecycle_state"] == "running"
    assert is_terminal_record(rec) is False


def test_is_terminal_record_handles_none():
    assert is_terminal_record(None) is False


# ─── reap_if_orphaned (#464) ──────────────────────────────────────────────────


async def test_reap_marks_vanished_job_stuck(store):
    await store.enqueue("jv")
    await store.grant_slot("jv")  # running, no terminal write
    rec = await vd.reap_if_orphaned(
        "jv", job_exists=False, job_active=False, store=store
    )
    assert rec is not None
    assert rec["lifecycle_state"] == "stuck"
    assert rec["error"]  # never-overclaim: a reaped job carries a reason
    assert "vanished" in rec["error"]


async def test_reap_marks_deadline_exceeded_no_verdict_stuck(store):
    await store.enqueue("jd")
    await store.grant_slot("jd")
    # Job object still present but finished (deadline/backoff) with no verdict.
    rec = await vd.reap_if_orphaned(
        "jd", job_exists=True, job_active=False, store=store
    )
    assert rec is not None
    assert rec["lifecycle_state"] == "stuck"
    assert "no verdict" in rec["error"]


async def test_reap_leaves_terminal_row_untouched(store):
    await store.enqueue("jdone")
    await store.grant_slot("jdone")
    await store.update_status("jdone", service_status="triaged", has_verdict=True)
    rec = await vd.reap_if_orphaned(
        "jdone", job_exists=False, job_active=False, store=store
    )
    assert rec is None  # idempotent — the Job's own terminal write wins
    assert (await store.get("jdone"))["lifecycle_state"] == "done"


async def test_reap_leaves_running_job_untouched(store):
    await store.enqueue("jrun")
    await store.grant_slot("jrun")
    rec = await vd.reap_if_orphaned(
        "jrun", job_exists=True, job_active=True, store=store
    )
    assert rec is None  # still running — nothing to reap
    assert (await store.get("jrun"))["lifecycle_state"] == "running"


async def test_reap_no_row_is_noop(store):
    assert (
        await vd.reap_if_orphaned(
            "ghost", job_exists=False, job_active=False, store=store
        )
        is None
    )


# ─── control-plane reconcile + reap tick (the wired loop's body) ──────────────


async def _probe(_results):
    async def _fn(namespace, job_name):
        return _results.get(job_name, (True, True))

    return _fn


async def _dispatch(store, job_id, correlation_key=None):
    return await vd.dispatch_verify_job(
        job_id=job_id,
        spec_dir=Path(f"/home/nonroot/.tfactory/ws/{job_id}/spec"),
        project_dir=Path(f"/home/nonroot/.tfactory/ws/{job_id}"),
        correlation_key=correlation_key,
        sandbox=_FakeSandbox(),
        store=store,
        apply_fn=_RecordingApply(),
    )


async def test_reconcile_tick_reaps_vanished_dispatched_job(store):
    d = await _dispatch(store, "proj:100")
    assert d is not None
    job_name = d.job_name
    probe_fn = await _probe({job_name: (False, False)})  # Job gone, row still active

    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=probe_fn)
    assert reaped == 1
    rec = await store.get("proj:100")
    assert rec["lifecycle_state"] == "stuck"
    assert "vanished" in (rec["error"] or "")


async def test_reconcile_tick_leaves_running_job(store):
    d = await _dispatch(store, "proj:101")
    probe_fn = await _probe({d.job_name: (True, True)})  # still active
    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=probe_fn)
    assert reaped == 0
    assert (await store.get("proj:101"))["lifecycle_state"] == "queued"


async def test_reconcile_tick_skips_terminal_row(store):
    await _dispatch(store, "proj:102")
    # The Job wrote its terminal verdict row (done) — the tick must not touch it.
    await store.update_status("proj:102", service_status="triaged", has_verdict=True)
    probe_fn = await _probe({})  # default (exists, active) — irrelevant once terminal
    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=probe_fn)
    assert reaped == 0
    assert (await store.get("proj:102"))["lifecycle_state"] == "done"


async def test_reconcile_tick_ignores_non_k8s_rows(store):
    # An in-pod verify row (no k8s-job worker_ref) is not the loop's concern.
    await store.enqueue("inpod-1")
    await store.grant_slot("inpod-1")
    reaped = await vd.reconcile_and_reap_once(
        store=store, probe_fn=await _probe({})
    )
    assert reaped == 0
    assert (await store.get("inpod-1"))["lifecycle_state"] == "running"


# ─── BUG 2: the verify Job gets a flake.nix at /work before nix develop ────────


def _write_nix_contract(spec_dir: Path) -> None:
    """Drop a task contract with a nix environment so materialize_flake fires."""
    import json

    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "task_contract.json").write_text(
        json.dumps(
            {
                "contract_version": "1",
                "environment": {
                    "language": "python",
                    "toolchain": {"python": "3.13"},
                    "provisioning": {"method": "nix", "generated": True},
                    "verify_commands": ["python -m pytest -q"],
                },
            }
        ),
        encoding="utf-8",
    )


async def test_dispatch_materializes_flake_into_project_before_apply(tmp_path, store):
    # The verify-orchestration Job runs `nix develop path:.../project#default`; the
    # per-task flake must be materialized into the project worktree BEFORE the Job,
    # else it fails exit=1 "flake.nix does not exist" (BUG 2).
    data_root = tmp_path / "data"
    project_dir = data_root / "workspaces" / "proj"
    spec_dir = project_dir / ".tfactory" / "specs" / "042"
    spec_dir.mkdir(parents=True, exist_ok=True)
    _write_nix_contract(spec_dir)

    sandbox = _FakeSandbox(namespace="factory", data_root=str(data_root))
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:042",
        spec_dir=spec_dir,
        project_dir=project_dir,
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    # The flake was materialized into the project worktree.
    assert (project_dir / "flake.nix").is_file()
    # And the applied Job develops THAT dir (project subpath), not the data root.
    _, manifest = apply.calls[0]
    cmd = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop path:/work/workspaces/proj#default" in cmd


async def test_dispatch_without_nix_manifest_runs_verify_directly(tmp_path, store):
    # No contract / no nix environment → no flake; the Job must run the verify
    # pipeline directly on the image rather than nix-developing a missing flake.
    data_root = tmp_path / "data"
    project_dir = data_root / "workspaces" / "proj"
    spec_dir = project_dir / ".tfactory" / "specs" / "043"
    spec_dir.mkdir(parents=True, exist_ok=True)

    sandbox = _FakeSandbox(namespace="factory", data_root=str(data_root))
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:043",
        spec_dir=spec_dir,
        project_dir=project_dir,
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    assert not (project_dir / "flake.nix").exists()
    _, manifest = apply.calls[0]
    cmd = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop" not in cmd
    assert "python -m agents.verify_pipeline" in cmd


# ─── BUG 1: the dispatch record succeeds across a private loop (no cross-loop) ──


def test_record_dispatch_succeeds_on_a_foreign_loop(tmp_path, monkeypatch):
    """The blocking dispatch path runs on its OWN loop (``asyncio.run`` in a worker
    thread); the durable store write must still succeed (BUG 1: asyncpg "Future
    attached to a different loop" when a main-loop-pinned pooled connection is
    reused from another loop). The fix makes ``_store_for`` open a fresh engine on
    the using loop. We drive the REAL ``DbJobStateStore`` that ``_store_for`` opens
    against a file-backed SQLite DB, across two independent ``asyncio.run`` loops —
    the production call shape — and assert the dispatch records a durable row that a
    later reconcile (on yet another loop) reads and marks terminal.

    (SQLite opens a connection per loop so it cannot reproduce asyncpg's exact
    loop-pinning failure; this test pins the *correct* behaviour the fresh-engine
    fix guarantees — the store write + reconcile succeed across loop boundaries —
    which is what stranded the verify before the fix.)
    """
    import asyncio

    db_path = tmp_path / "loop.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    # Create the schema once (on a throwaway loop) so the store has a table.
    async def _make_schema() -> None:
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await eng.dispose()

    asyncio.run(_make_schema())

    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()

    # Run the dispatch on a fresh loop — _store_for must open its engine on THIS
    # loop (not reuse a main-loop-pinned one), so the write doesn't raise.
    async def _go() -> object:
        return await vd.dispatch_verify_job(
            job_id="loop:1",
            spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
            project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
            sandbox=sandbox,
            store=None,  # exercise the real _store_for fresh-engine path
            apply_fn=apply,
        )

    result = asyncio.run(_go())
    assert result is not None  # dispatch recorded + applied without a loop error

    # The durable row landed; reconcile (also on its own loop) can read it and a
    # terminal write marks it done — the reaper/reconciler can now produce a verdict.
    async def _verdict() -> str:
        rec = await vd.reconcile_verify_job("loop:1")
        assert rec is not None
        assert rec["lifecycle_state"] == "queued"
        assert rec["worker_ref"]["kind"] == "k8s-job"
        # The Job writes its terminal row; emulate that write, then reconcile.
        async with vd._store_for(None) as (s, _owned):
            await s.update_status("loop:1", service_status="triaged", has_verdict=True)
        done = await vd.reconcile_verify_job("loop:1")
        return done["lifecycle_state"]

    assert asyncio.run(_verdict()) == "done"
