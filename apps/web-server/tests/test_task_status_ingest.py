"""GET /api/tasks/{id}/status reads the spec workspace for ingest tasks (#1597)."""

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _spec(tmp_path: Path, project: str, spec: str, **status: Any) -> Path:
    d = tmp_path / "workspaces" / project / "specs" / spec
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps(status))
    return d


@pytest.fixture
def svc() -> MagicMock:
    m = MagicMock()
    m.is_running.return_value = False
    return m


@pytest.fixture
def ex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, svc: MagicMock
) -> Iterator[ModuleType]:
    execution = importlib.import_module("server.routes.execution")

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    with patch.object(execution, "get_agent_service", return_value=svc):
        yield execution


def _status(ex: ModuleType, task_id: str) -> Any:
    return asyncio.run(ex.get_task_status(task_id))


def test_running_ingest_task_reports_phase_and_running(
    ex: ModuleType, tmp_path: Path
) -> None:
    _spec(tmp_path, "p1", "s1", status="generating", phase="gen_functional")
    r = _status(ex, "p1:s1")
    assert r.is_running is True
    assert r.phase == "gen_functional"


def test_terminal_task_is_not_running_and_carries_reason(
    ex: ModuleType, tmp_path: Path
) -> None:
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


def test_unknown_status_keeps_metadata_but_not_running(
    ex: ModuleType, tmp_path: Path
) -> None:
    _spec(tmp_path, "p1", "s1", status="weird", phase="x")
    r = _status(ex, "p1:s1")
    assert (r.is_running, r.phase) == (False, "x")


def test_missing_or_malformed_file_falls_back(ex: ModuleType, tmp_path: Path) -> None:
    assert _status(ex, "p1:nope").is_running is False
    d = _spec(tmp_path, "p1", "bad", status="x")
    (d / "status.json").write_text("{not json")
    r = _status(ex, "p1:bad")
    assert (r.is_running, r.phase) == (False, None)


def test_in_process_wins_over_inactive_file(
    ex: ModuleType, svc: MagicMock, tmp_path: Path
) -> None:
    _spec(tmp_path, "p1", "s1", status="triaged")
    svc.is_running.return_value = True
    assert _status(ex, "p1:s1").is_running is True


def test_bare_ingest_id_is_read_from_workspace(ex: ModuleType, tmp_path: Path) -> None:
    # Real ingest layout: task_id is the bare spec_id, workspace keyed by uuid.
    _spec(tmp_path, "uuid-1", "l1-pr610", status="generating", phase="gen_x")
    with patch.object(ex, "load_projects", return_value={"uuid-1": {}}):
        r = _status(ex, "l1-pr610")
    assert (r.is_running, r.phase) == (True, "gen_x")


def test_bare_id_of_unregistered_or_ambiguous_workspace_reveals_nothing(
    ex: ModuleType, tmp_path: Path
) -> None:
    _spec(tmp_path, "stray", "s1", status="generating", phase="secret")
    _spec(tmp_path, "a", "dup", status="generating", phase="pa")
    _spec(tmp_path, "b", "dup", status="generating", phase="pb")
    with patch.object(ex, "load_projects", return_value={"a": {}, "b": {}}):
        assert _status(ex, "s1").phase is None
        assert _status(ex, "dup").phase is None


def test_project_name_prefix_resolves_to_uuid_workspace(
    ex: ModuleType, tmp_path: Path
) -> None:
    _spec(tmp_path, "uuid-1", "s1", status="generating", phase="p")
    with patch.object(
        ex, "load_projects", return_value={"uuid-1": {"name": "my-proj"}}
    ):
        assert _status(ex, "my-proj:s1").phase == "p"


def test_project_name_is_authorized_as_the_uuid(ex: ModuleType, tmp_path: Path) -> None:
    _spec(tmp_path, "uuid-1", "s1", status="generating", phase="p")
    seen: list[str] = []

    class _Rec:
        async def __call__(self, task_id: str, *_a: Any) -> None:
            seen.append(task_id)

    app = FastAPI()
    app.include_router(ex.router, prefix="/api/tasks")
    app.dependency_overrides[ex.get_db] = lambda: None
    with (
        patch.object(ex, "require_task_access", return_value=_Rec()),
        patch.object(ex, "load_projects", return_value={"uuid-1": {"name": "my-proj"}}),
    ):
        assert TestClient(app).get("/api/tasks/my-proj:s1/status").status_code == 200
        assert TestClient(app).get("/api/tasks/s1/status").status_code == 200
    assert seen == ["uuid-1:s1", "uuid-1:s1"]


def test_traversal_in_spec_part_is_rejected(ex: ModuleType) -> None:
    with pytest.raises(HTTPException) as e:
        _status(ex, "p1:..")
    assert e.value.status_code == 400


def test_denied_caller_gets_nothing_and_unowned_bare_id_skips_check(
    ex: ModuleType, tmp_path: Path
) -> None:
    _spec(tmp_path, "p1", "s1", status="generating", phase="secret-phase")
    app = FastAPI()
    app.include_router(ex.router, prefix="/api/tasks")
    app.dependency_overrides[ex.get_db] = lambda: None

    class _Deny:
        async def __call__(self, *_a: Any) -> None:
            raise HTTPException(status_code=403, detail="no")

    with patch.object(ex, "require_task_access", return_value=_Deny()):
        c = TestClient(app)
        r = c.get("/api/tasks/p1:s1/status")
        assert r.status_code == 403 and "secret-phase" not in r.text
        # a bare id owned by no registered project reveals nothing and needs no check
        r = c.get("/api/tasks/s1/status")
        assert r.status_code == 200 and "secret-phase" not in r.text
        with patch.object(ex, "load_projects", return_value={"p1": {}}):
            assert c.get("/api/tasks/s1/status").status_code == 403


def test_wrongly_typed_fields_fall_back_instead_of_crashing(
    ex: ModuleType, tmp_path: Path
) -> None:
    _spec(tmp_path, "p1", "s1", status=[], phase={"a": 1}, failed_reason=3)
    r = _status(ex, "p1:s1")
    assert (r.is_running, r.phase, r.message) == (False, None, None)
