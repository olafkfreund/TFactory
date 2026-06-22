"""Tests for the extracted task-logs sub-router — #360 (god-file split).

Mounts ``routes/tasks_logs.py`` on a bare app under the same ``/api/tasks``
prefix main.py uses, so the extracted endpoints keep their exact paths.
Skipped automatically in venvs without FastAPI (see tests/conftest.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.routes import tasks_logs  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    project_path = tmp_path / "proj"
    (project_path).mkdir()
    monkeypatch.setattr(
        tasks_logs,
        "load_projects",
        lambda: {"p1": {"path": str(project_path)}},
    )
    app = FastAPI()
    app.include_router(tasks_logs.router, prefix="/api/tasks")
    return TestClient(app), project_path


def test_watch_unwatch_stubs(client):
    cl, _ = client
    assert cl.post("/api/tasks/p1:s1/logs/watch").json()["success"] is True
    assert cl.post("/api/tasks/p1:s1/logs/unwatch").json()["success"] is True


def test_logs_invalid_task_id(client):
    cl, _ = client
    assert cl.get("/api/tasks/no-colon/logs").status_code == 400


def test_logs_unknown_project(client):
    cl, _ = client
    assert cl.get("/api/tasks/nope:s1/logs").status_code == 404


def test_logs_returns_phases_from_task_logs_json(client):
    cl, project_path = client
    spec = project_path / ".tfactory" / "specs" / "s1"
    spec.mkdir(parents=True)
    (spec / "task_logs.json").write_text(
        json.dumps({"spec_id": "s1", "phases": {"planning": {"lines": ["a"]}}})
    )
    resp = cl.get("/api/tasks/p1:s1/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["specId"] == "s1"
    assert "planning" in body["phases"]


def test_logs_fallback_when_no_task_logs(client):
    cl, project_path = client
    spec = project_path / ".tfactory" / "specs" / "s1"
    spec.mkdir(parents=True)
    resp = cl.get("/api/tasks/p1:s1/logs")
    assert resp.status_code == 200
    assert resp.json() == {"logs": [], "total": 0}
