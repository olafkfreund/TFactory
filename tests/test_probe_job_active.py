"""A present verify Job stays active until Kubernetes reports a terminal result."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from agents import verify_dispatch as vd  # noqa: E402
from server.database.models import Base  # noqa: E402
from server.services.job_state_store import get_job_state_store  # noqa: E402


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
    async with factory() as session:
        yield get_job_state_store(session)
    await engine.dispose()


def _fake_jobs(monkeypatch, jobs):
    class _Batch:
        async def read_namespaced_job(self, name, _namespace):
            return jobs[name]

    class _Api:
        async def close(self):
            return None

    async def _batch():
        return _Api(), _Batch()

    monkeypatch.setattr(vd, "_k8s_batch", _batch)


@pytest.mark.asyncio
async def test_fresh_job_without_status_is_active(monkeypatch):
    _fake_jobs(monkeypatch, {"fresh": SimpleNamespace()})
    assert await vd._probe_job("factory", "fresh") == (True, True, False)


@pytest.mark.asyncio
async def test_none_status_fields_are_active(monkeypatch):
    job = SimpleNamespace(
        status=SimpleNamespace(active=None, succeeded=None, conditions=None)
    )
    _fake_jobs(monkeypatch, {"pending": job})
    assert await vd._probe_job("factory", "pending") == (True, True, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            SimpleNamespace(
                active=0,
                succeeded=0,
                conditions=[SimpleNamespace(type="Complete", status="True")],
            ),
            (True, False, True),
        ),
        (
            SimpleNamespace(
                active=0,
                succeeded=0,
                conditions=[SimpleNamespace(type="Failed", status="True")],
            ),
            (True, False, False),
        ),
        (
            SimpleNamespace(
                active=1,
                succeeded=0,
                conditions=[SimpleNamespace(type="Complete", status="False")],
            ),
            (True, True, False),
        ),
        (
            SimpleNamespace(
                active=1,
                succeeded=0,
                conditions=[SimpleNamespace(type="Failed", status="Unknown")],
            ),
            (True, True, False),
        ),
        (
            SimpleNamespace(active=1, succeeded=0, failed=1, conditions=None),
            (True, True, False),
        ),
        (SimpleNamespace(active=0, succeeded=1, conditions=None), (True, False, True)),
    ],
    ids=[
        "complete",
        "failed",
        "false-condition",
        "unknown-condition",
        "failed-pod-retry",
        "succeeded-pod",
    ],
)
async def test_job_terminal_evidence(monkeypatch, status, expected):
    _fake_jobs(monkeypatch, {"job": SimpleNamespace(status=status)})
    assert await vd._probe_job("factory", "job") == expected


@pytest.mark.asyncio
async def test_reconcile_keeps_fresh_job_and_reaps_failed_job(
    monkeypatch, store, tmp_path
):
    _fake_jobs(
        monkeypatch,
        {
            "fresh": SimpleNamespace(status=SimpleNamespace(active=0)),
            "failed": SimpleNamespace(
                status=SimpleNamespace(
                    active=0,
                    succeeded=0,
                    conditions=[SimpleNamespace(type="Failed", status="True")],
                )
            ),
        },
    )
    for name in ("fresh", "failed"):
        await store.enqueue(name)
        await store.grant_slot(name)
        await store.update_status(
            name,
            service_status="running",
            has_verdict=False,
            worker_ref={"kind": "k8s-job", "job_name": name},
        )

    assert await vd.reconcile_and_reap_once(store=store, data_root=str(tmp_path)) == 1
    assert (await store.get("fresh"))["lifecycle_state"] == "running"
    assert (await store.get("failed"))["lifecycle_state"] == "stuck"
