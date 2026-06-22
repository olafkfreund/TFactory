"""Tests for the regression portal route — RFC-0018 #489 (part 2).

GET /api/projects/{project_id}/regression. Skipped automatically in venvs
without FastAPI (see tests/conftest.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    RegressionRun,
    TestOutcome,
    TestStatus,
    regression_dir,
    save_run,
)
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.routes import regression as regression_route  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(regression_route.router)
    return TestClient(app), tmp_path


def test_empty_project_returns_valid_shape(client):
    cl, _ = client
    resp = cl.get("/api/projects/demo/regression")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_run_id"] is None
    assert body["runs"] == []
    assert body["quarantined"] == []


def test_trigger_run_schedules_and_returns_run_id(client, monkeypatch):
    cl, root = client
    calls = []
    import server.routes.regression as route_mod

    monkeypatch.setattr(
        route_mod, "run_for_project", lambda config, **kw: calls.append((config, kw))
    )
    resp = cl.post("/api/projects/demo/regression/run")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["run_id"].startswith("run-")
    # TestClient executes background tasks after the response
    assert len(calls) == 1
    cfg, kw = calls[0]
    assert cfg.project_id == "demo"
    assert cfg.workspace_root == root
    assert cfg.repo_root == root / "demo"
    assert "now" in kw


def test_trigger_run_rejects_invalid_id(client):
    cl, _ = client
    resp = cl.post("/api/projects/bad id!/regression/run")
    assert resp.status_code == 400


def test_returns_seeded_run(client):
    cl, root = client
    reg = regression_dir(root, "demo")
    save_run(
        reg,
        RegressionRun(
            run_id="r1",
            project_id="demo",
            ran_at="2026-06-22T12:00:00Z",
            results=(TestOutcome("a", "unit", "pytest", TestStatus.PASSED),),
            commit="abc",
        ),
    )
    resp = cl.get("/api/projects/demo/regression")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_run_id"] == "r1"
    assert [r["run_id"] for r in body["runs"]] == ["r1"]
    assert body["latest"]["commit"] == "abc"


def test_rejects_path_traversal_project_id(client):
    cl, _ = client
    resp = cl.get("/api/projects/..%2f..%2fetc/regression")
    # the id contains '/' after decoding -> no route match (404) or 400 guard
    assert resp.status_code in (400, 404)


def test_rejects_invalid_slug(client):
    cl, _ = client
    resp = cl.get("/api/projects/bad id!/regression")
    assert resp.status_code == 400
