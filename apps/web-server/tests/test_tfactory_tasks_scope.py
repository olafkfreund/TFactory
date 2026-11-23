"""Project-qualified portal task routes and rerun behavior."""

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

for root in (
    Path(__file__).resolve().parents[1],
    Path(__file__).resolve().parents[2] / "backend",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from server.routes import project_authz, projects, tfactory_tasks  # noqa: E402


@pytest.fixture
def scoped_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setattr(
        projects,
        "load_projects",
        lambda: {
            "uuid-a": {"name": "Workspace A", "path": str(tmp_path / "a")},
            "uuid-b": {"name": "Workspace B", "path": str(tmp_path / "b")},
        },
    )
    monkeypatch.setattr(project_authz, "_load_projects", projects.load_projects)

    async def visible(principal, db=None, min_role="viewer"):
        return {"uuid-a", "uuid-b"} if principal["role"] != "invisible" else set()

    async def authorize(principal, pid, min_role="viewer", db=None):
        if pid not in await visible(principal):
            raise HTTPException(404, "Project not found")
        if min_role == "member" and principal["role"] == "viewer":
            raise HTTPException(403, "Forbidden")

    monkeypatch.setattr(tfactory_tasks, "visible_project_ids", visible)
    monkeypatch.setattr(tfactory_tasks, "require_project_role", authorize)
    for pid in ("uuid-a", "uuid-b"):
        spec_dir = tmp_path / "workspaces" / pid / "specs" / "same-spec"
        spec_dir.mkdir(parents=True)
        (spec_dir / "status.json").write_text(
            json.dumps({"status": "failed", "dismissed": False})
        )

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user = {"role": request.headers.get("X-Role", "member")}
        return await call_next(request)

    app.include_router(tfactory_tasks.router, prefix="/api/tfactory/tasks")
    with TestClient(app) as client:
        yield client, tmp_path


def test_tfactory_tasks_filter_uuid_and_workspace_name(scoped_tasks):
    client, _ = scoped_tasks
    for ref in ("uuid-a", "Workspace A"):
        response = client.get("/api/tfactory/tasks", params={"project_id": ref})
        assert response.status_code == 200
        assert [row["project_id"] for row in response.json()["tasks"]] == ["uuid-a"]


def test_tfactory_tasks_duplicate_spec_ids_require_project(scoped_tasks):
    client, _ = scoped_tasks
    assert client.get("/api/tfactory/tasks/same-spec").status_code == 409
    response = client.get(
        "/api/tfactory/tasks/same-spec", params={"project_id": "uuid-b"}
    )
    assert response.json()["project_id"] == "uuid-b"


def test_tfactory_tasks_websocket_logs_use_project(scoped_tasks, monkeypatch):
    client, root = scoped_tasks
    for pid in ("uuid-a", "uuid-b"):
        logs = root / "workspaces" / pid / "specs" / "same-spec" / "logs"
        logs.mkdir()
        (logs / "planner.log").write_text(pid + "\n")

    async def authenticate(_websocket):
        return {"role": "member"}

    monkeypatch.setattr(tfactory_tasks, "authenticate_websocket", authenticate)
    with client.websocket_connect(
        "/api/tfactory/tasks/same-spec/logs/stream?project_id=uuid-b"
    ) as websocket:
        assert json.loads(websocket.receive_text())["files"]["planner"] == ["uuid-b"]


def test_tfactory_tasks_rerun_auth_active_and_dismiss(scoped_tasks):
    client, root = scoped_tasks
    url = "/api/tfactory/tasks/same-spec"
    params = {"project_id": "uuid-a"}
    assert (
        client.post(
            url + "/rerun", params=params, headers={"X-Role": "viewer"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            url + "/rerun", params=params, headers={"X-Role": "invisible"}
        ).status_code
        == 404
    )
    assert client.post(url + "/dismiss", params=params).status_code == 200
    listed = client.get("/api/tfactory/tasks", params=params).json()["tasks"]
    assert listed[0]["dismissed"] is True
    response = client.post(url + "/rerun", params=params)
    assert response.status_code == 200
    assert response.json()["planner_scheduled"] is False
    assert response.json()["planner_reason"]
    assert (
        client.get("/api/tfactory/tasks", params=params).json()["tasks"][0]["dismissed"]
        is False
    )
    assert client.post(url + "/rerun", params=params).status_code == 409
    assert (
        json.loads(
            (root / "workspaces/uuid-a/specs/same-spec/status.json").read_text()
        )["status"]
        == "pending"
    )
