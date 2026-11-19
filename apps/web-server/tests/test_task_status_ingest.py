"""GET /api/tasks/{id}/status reads the spec workspace for ingest tasks (#1597)."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _spec(tmp_path, project, spec, **status):
    d = tmp_path / "workspaces" / project / "specs" / spec
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps(status))
    return d


@pytest.fixture
def ex(tmp_path, monkeypatch):
    from server.routes import execution  # noqa: PLC0415

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    svc = MagicMock()
    svc.is_running.return_value = False
    with patch.object(execution, "get_agent_service", return_value=svc):
        execution._svc = svc
        yield execution


def _status(ex, task_id):
    return asyncio.run(ex.get_task_status(task_id))


def test_running_ingest_task_reports_phase_and_running(ex, tmp_path):
    _spec(tmp_path, "p1", "s1", status="generating", phase="gen_functional")
    r = _status(ex, "p1:s1")
    assert r.is_running is True
    assert r.phase == "gen_functional"


def test_terminal_task_is_not_running_and_carries_reason(ex, tmp_path):
    _spec(
        tmp_path,
        "p1",
        "s1",
        status="failed",
        phase="verify_job_reaped",
        reaped_reason="r",
        failed_reason="f",
    )
    r = _status(ex, "p1:s1")
    assert r.is_running is False
    assert r.message == "f"  # failed_reason wins over reaped_reason


def test_unknown_status_keeps_metadata_but_not_running(ex, tmp_path):
    _spec(tmp_path, "p1", "s1", status="weird", phase="x")
    r = _status(ex, "p1:s1")
    assert (r.is_running, r.phase) == (False, "x")


def test_missing_or_malformed_file_falls_back(ex, tmp_path):
    assert _status(ex, "p1:nope").is_running is False
    d = _spec(tmp_path, "p1", "bad", status="x")
    (d / "status.json").write_text("{not json")
    r = _status(ex, "p1:bad")
    assert (r.is_running, r.phase) == (False, None)


def test_in_process_wins_over_inactive_file(ex, tmp_path):
    _spec(tmp_path, "p1", "s1", status="triaged")
    ex._svc.is_running.return_value = True
    assert _status(ex, "p1:s1").is_running is True


def test_bare_id_is_not_read_from_workspace(ex, tmp_path):
    _spec(tmp_path, "p1", "s1", status="generating")
    assert _status(ex, "s1").is_running is False


def test_traversal_in_spec_part_is_rejected(ex):
    with pytest.raises(HTTPException) as e:
        _status(ex, "p1:..")
    assert e.value.status_code == 400


def test_denied_caller_gets_nothing_and_bare_id_skips_check(ex, tmp_path):
    _spec(tmp_path, "p1", "s1", status="generating", phase="secret-phase")
    app = FastAPI()
    app.include_router(ex.router, prefix="/api/tasks")
    app.dependency_overrides[ex.get_db] = lambda: None

    class _Deny:
        async def __call__(self, *_a):
            raise HTTPException(status_code=403, detail="no")

    with patch.object(ex, "require_task_access", return_value=_Deny()):
        c = TestClient(app)
        r = c.get("/api/tasks/p1:s1/status")
        assert r.status_code == 403 and "secret-phase" not in r.text
        assert c.get("/api/tasks/s1/status").status_code == 200


def test_wrongly_typed_fields_fall_back_instead_of_crashing(ex, tmp_path):
    _spec(tmp_path, "p1", "s1", status=[], phase={"a": 1}, failed_reason=3)
    r = _status(ex, "p1:s1")
    assert (r.is_running, r.phase, r.message) == (False, None, None)
