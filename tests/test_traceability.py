"""RFC-0015 §4 D2: the requirement -> test -> VAL traceability matrix.

A verify with N acceptance criteria + their mapped tests must produce N
traceability rows, each carrying the right ``ac_id`` / ``tests`` / ``val_level`` /
``status``; an uncovered AC degrades to ``tests: []`` + ``status: not_run`` (an
honest gap, never hidden). The matrix is also persisted onto the durable
``job_states`` row (the #468 store / #465 verification data) so CFactory (#126)
can render it straight from Postgres — exercised here against the real
``DbJobStateStore`` on in-memory SQLite, matching ``test_job_state_store.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from agents.ac_fidelity import build_ac_ledger
from agents.traceability import build_traceability
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.database.models import Base  # noqa: E402
from server.services import job_state_status as st  # noqa: E402
from server.services.job_state_store import get_job_state_store  # noqa: E402

# ─── fixtures: a 4-AC plan + verdicts (one AC deliberately uncovered) ─────────

_PLAN = {
    "phases": [
        {
            "phase": 1,
            "name": "AC#1: GET / returns 200 HTML",
            "subtasks": [{"id": "root-200-unit"}, {"id": "root-200-browser"}],
        },
        {
            "phase": 2,
            "name": "AC#2: h1 says Hello",
            "subtasks": [{"id": "h1-browser"}],
        },
        {
            "phase": 3,
            "name": "AC#3: ping button updates result",
            "subtasks": [{"id": "ping-browser"}],
        },
        {
            "phase": 4,
            "name": "AC#4: health returns ok",  # no verdict -> uncovered
            "subtasks": [{"id": "health-unit"}],
        },
    ]
}

_VERDICTS = [
    {
        "test_id": "root-200-unit",
        "test_file": "tests/unit/root.py",
        "verdict": "accept",
        "lane": "unit",
    },
    {
        "test_id": "root-200-browser",
        "test_file": "tests/e2e/root.spec.ts",
        "verdict": "accept",
        "lane": "browser",
    },
    {
        "test_id": "h1-browser",
        "test_file": "tests/e2e/h1.spec.ts",
        "verdict": "flag",
        "lane": "browser",
    },
    {
        "test_id": "ping-browser",
        "test_file": "tests/e2e/ping.spec.ts",
        "verdict": "reject",
        "lane": "browser",
    },
    # AC#4 (health-unit) has NO verdict -> not_run (the traceability gap).
]

# A run that reached VAL-2 (the gate-recomputed achieved level).
_BLOCK = {"target_level": "VAL-2", "achieved_level": "VAL-2", "levels": []}


def _matrix() -> dict[str, dict]:
    ledger = build_ac_ledger(_PLAN, _VERDICTS)
    rows = build_traceability(ledger, _BLOCK)
    return {r["ac_id"]: r for r in rows}


# ─── shape: N ACs -> N rows ───────────────────────────────────────────────────


def test_one_row_per_ac():
    rows = build_traceability(build_ac_ledger(_PLAN, _VERDICTS), _BLOCK)
    assert len(rows) == 4  # one per AC, replan phases excluded
    assert {r["ac_id"] for r in rows} == {"AC#1", "AC#2", "AC#3", "AC#4"}
    # Every row carries the required contract keys.
    for r in rows:
        assert set(r) >= {"ac_id", "tests", "val_level", "status"}
        assert r["status"] in {"passed", "failed", "not_run", "skipped"}


# ─── status mapping is honest ─────────────────────────────────────────────────


def test_passed_ac_carries_tests_and_achieved_val():
    by = _matrix()
    ac1 = by["AC#1"]
    assert ac1["status"] == "passed"  # >=1 accepted test
    assert ac1["val_level"] == "VAL-2"  # inherits the run's achieved level
    # Both covering tests are referenced as file::id.
    assert "tests/unit/root.py::root-200-unit" in ac1["tests"]
    assert "tests/e2e/root.spec.ts::root-200-browser" in ac1["tests"]
    assert ac1["ac_text"] == "GET / returns 200 HTML"


def test_flagged_only_ac_is_skipped_not_passed():
    ac2 = _matrix()["AC#2"]
    assert ac2["status"] == "skipped"  # covered, only flagged -> needs review
    assert ac2["val_level"] == "VAL-0"  # must not borrow the ceiling
    assert ac2["tests"] == ["tests/e2e/h1.spec.ts::h1-browser"]


def test_rejected_only_ac_is_failed():
    ac3 = _matrix()["AC#3"]
    assert ac3["status"] == "failed"  # every covering test rejected
    assert ac3["val_level"] == "VAL-0"
    assert ac3["tests"] == ["tests/e2e/ping.spec.ts::ping-browser"]


def test_uncovered_ac_degrades_to_not_run_with_empty_tests():
    ac4 = _matrix()["AC#4"]
    assert ac4["status"] == "not_run"  # no verdict for its test at all
    assert ac4["tests"] == []  # the honest traceability gap
    assert ac4["val_level"] == "VAL-0"


# ─── graceful degradation ─────────────────────────────────────────────────────


def test_absent_ledger_yields_empty_matrix():
    assert build_traceability(None, _BLOCK) == []
    assert build_traceability({}, _BLOCK) == []
    assert build_traceability({"acceptance": []}, _BLOCK) == []


def test_missing_verification_block_pins_val0_but_still_emits():
    rows = build_traceability(build_ac_ledger(_PLAN, _VERDICTS), None)
    by = {r["ac_id"]: r for r in rows}
    assert len(rows) == 4
    # No block -> no achieved level -> even a passed AC is honestly VAL-0.
    assert by["AC#1"]["status"] == "passed"
    assert by["AC#1"]["val_level"] == "VAL-0"


# ─── persistence onto the durable job_states row (real store, SQLite) ─────────


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_traceability_persists_onto_durable_result(session):
    """A terminal verify with traceability records it in the row's result JSON."""
    store = get_job_state_store(session)
    await store.enqueue("spec-trace")
    await store.grant_slot("spec-trace")

    matrix = build_traceability(build_ac_ledger(_PLAN, _VERDICTS), _BLOCK)
    rec = await store.update_status(
        "spec-trace",
        service_status="triaged",
        has_verdict=True,
        result={"status": "triaged", "traceability": matrix},
    )

    assert rec["lifecycle_state"] == st.DONE
    persisted = rec["result"]["traceability"]
    assert len(persisted) == 4
    by = {r["ac_id"]: r for r in persisted}
    assert by["AC#1"]["status"] == "passed"
    assert by["AC#4"]["status"] == "not_run" and by["AC#4"]["tests"] == []
