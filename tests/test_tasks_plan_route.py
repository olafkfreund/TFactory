"""Extracted plan-approval sub-router — #360 (god-file split).

Asserts the wiring is unchanged (both plan endpoints still mounted at the same
URLs) plus the cheap early-validation paths. Skipped in venvs without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.routes import tasks_plan  # noqa: E402


def test_plan_routes_registered():
    app = FastAPI()
    app.include_router(tasks_plan.router, prefix="/api/tasks")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/tasks/{task_id}/approve-plan", "POST") in have
    assert ("/api/tasks/{task_id}/reject-plan", "POST") in have


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_plan, "load_projects", lambda: {})
    app = FastAPI()
    app.include_router(tasks_plan.router, prefix="/api/tasks")
    return TestClient(app)


def test_approve_plan_unknown_project_404(client):
    # project not in the (empty) registry -> 404
    assert client.post("/api/tasks/nope:s1/approve-plan").status_code == 404


def test_reject_plan_unknown_project_404(client):
    assert client.post("/api/tasks/nope:s1/reject-plan").status_code == 404
