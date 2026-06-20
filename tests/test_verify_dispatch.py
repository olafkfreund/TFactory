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
    assert "nix develop path:/work#default" in cmd
    assert "python -m agents.verify_pipeline" in cmd
    assert "--spec /work/workspaces/proj/.tfactory/specs/042" in cmd
    assert "--project /work/workspaces/proj" in cmd
    assert "--job-id proj-abc:042-verify" in cmd
    assert "--correlation-key 482" in cmd


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
    result = await vd.dispatch_verify_job(
        job_id="proj:042-verify",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        correlation_key=99,
        sandbox=sandbox,
        store=store,
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
