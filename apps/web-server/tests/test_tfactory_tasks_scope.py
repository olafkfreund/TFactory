"""Project-qualified portal task routes and rerun behavior."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

for root in (
    Path(__file__).resolve().parents[1],
    Path(__file__).resolve().parents[2] / "backend",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from server.auth import WebSocketAuthError  # noqa: E402
from server.routes import project_authz, projects, tfactory_tasks  # noqa: E402


@pytest.fixture
def scoped_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setattr(
        projects,
        "load_projects",
        lambda: {
            "uuid-a": {
                "name": "Workspace A",
                "path": str(tmp_path / "a"),
                "org_id": "A",
            },
            "uuid-b": {
                "name": "Workspace B",
                "path": str(tmp_path / "b"),
                "org_id": "A",
            },
            "uuid-c": {
                "name": "Workspace A",
                "path": str(tmp_path / "c"),
                "org_id": "B",
            },
        },
    )
    monkeypatch.setattr(project_authz, "_load_projects", projects.load_projects)
    monkeypatch.setattr(
        project_authz,
        "get_settings",
        lambda: SimpleNamespace(
            DISABLE_AUTH=False,
            PROJECTS_BACKEND="json",
            LEGACY_SHARED_TOKEN_FULL_ACCESS=False,
        ),
    )
    role: list[str] = ["owner"]
    db = AsyncMock()
    db.execute.side_effect = lambda *_: SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(role=role[0])
    )

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr("server.database.engine.async_session_factory", session)
    for pid in ("uuid-a", "uuid-b"):
        spec_dir = tmp_path / "workspaces" / pid / "specs" / "same-spec"
        spec_dir.mkdir(parents=True)
        (spec_dir / "status.json").write_text(
            json.dumps({"status": "failed", "dismissed": False})
        )

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        selected = request.headers.get("X-Role", "owner")
        role[0] = "viewer" if selected == "viewer" else "owner"
        request.state.user = {
            "id": selected,
            "org_id": "Z" if selected == "invisible" else "A",
        }
        if selected == "scoped-owner":
            request.state.user.update(api_key_id="read-key", scopes=["mcp:read"])
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


def test_same_name_in_other_org_does_not_break_resolution(
    scoped_tasks: tuple[TestClient, Path],
) -> None:
    client, _ = scoped_tasks
    assert (
        client.get("/api/tfactory/tasks", params={"project_id": "Workspace A"}).json()[
            "tasks"
        ][0]["project_id"]
        == "uuid-a"
    )
    assert (
        client.get(
            "/api/tfactory/tasks/same-spec", params={"project_id": "Workspace A"}
        ).json()["project_id"]
        == "uuid-a"
    )
    assert (
        client.get("/api/tfactory/tasks", params={"project_id": "uuid-c"}).status_code
        == 404
    )
    assert (
        client.get("/api/tfactory/tasks", params={"project_id": "missing"}).json()
        == client.get("/api/tfactory/tasks", params={"project_id": "uuid-c"}).json()
    )


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
        return {"id": "owner", "org_id": "A"}

    monkeypatch.setattr(tfactory_tasks, "authenticate_websocket", authenticate)
    with client.websocket_connect(
        "/api/tfactory/tasks/same-spec/logs/stream?project_id=uuid-b"
    ) as websocket:
        assert json.loads(websocket.receive_text())["files"]["planner"] == ["uuid-b"]
    with client.websocket_connect(
        "/api/tfactory/tasks/same-spec/logs/stream?project_id=Workspace%20A"
    ) as websocket:
        assert json.loads(websocket.receive_text())["files"]["planner"] == ["uuid-a"]


def test_websocket_logs_auth_failure_closes_after_accept(scoped_tasks, monkeypatch):
    client, _ = scoped_tasks

    async def reject(websocket):
        await websocket.close(code=4001, reason="Unauthorized")
        raise WebSocketAuthError("No token provided")

    monkeypatch.setattr(tfactory_tasks, "authenticate_websocket", reject)
    with (
        client.websocket_connect(
            "/api/tfactory/tasks/same-spec/logs/stream"
        ) as websocket,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        websocket.receive_text()
    assert exc.value.code == 4001


def test_websocket_logs_role_denied_closes_4003(scoped_tasks, monkeypatch):
    client, _ = scoped_tasks

    async def authenticate(_websocket):
        return {"id": "owner", "org_id": "A"}

    async def deny(*_args):
        raise HTTPException(403, "Forbidden")

    monkeypatch.setattr(tfactory_tasks, "authenticate_websocket", authenticate)
    monkeypatch.setattr(tfactory_tasks, "require_project_role", deny)
    with (
        client.websocket_connect(
            "/api/tfactory/tasks/same-spec/logs/stream?project_id=uuid-a"
        ) as websocket,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        websocket.receive_text()
    assert exc.value.code == 4003


def test_list_tasks_unknown_and_invisible_project_match(scoped_tasks):
    client, _ = scoped_tasks
    responses = [
        client.get("/api/tfactory/tasks", params={"project_id": project_id})
        for project_id in ("missing", "uuid-c")
    ]
    assert [response.status_code for response in responses] == [404, 404]
    assert responses[0].json() == responses[1].json() == {"detail": "Project not found"}


def test_websocket_logs_unknown_and_invisible_project_match(scoped_tasks, monkeypatch):
    client, _ = scoped_tasks

    async def authenticate(_websocket):
        return {"id": "owner", "org_id": "A"}

    monkeypatch.setattr(tfactory_tasks, "authenticate_websocket", authenticate)
    errors = []
    for project_id in ("missing", "uuid-c"):
        with (
            client.websocket_connect(
                f"/api/tfactory/tasks/same-spec/logs/stream?project_id={project_id}"
            ) as websocket,
            pytest.raises(WebSocketDisconnect) as exc,
        ):
            websocket.receive_text()
        errors.append((exc.value.code, exc.value.reason))
    assert errors == [(4404, "Project not found")] * 2


def test_accept_visual_baseline_uses_project_id(scoped_tasks, monkeypatch):
    client, _ = scoped_tasks
    monkeypatch.setattr(
        tfactory_tasks,
        "accept_visual_baseline",
        lambda _spec, _target, _snapshot, _body, visible: {"visible": sorted(visible)},
    )
    url = "/api/tfactory/tasks/same-spec/visual-baselines/browser/home.png/accept"
    body = {"source": "findings/screenshots/home.png"}
    response = client.post(url, params={"project_id": "uuid-b"}, json=body)
    assert response.status_code == 200
    assert response.json() == {"visible": ["uuid-b"]}
    unknown = client.post(url, params={"project_id": "missing"}, json=body)
    invisible = client.post(url, params={"project_id": "uuid-c"}, json=body)
    assert unknown.status_code == invisible.status_code == 404
    assert unknown.json() == invisible.json() == {"detail": "Project not found"}


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
            url + "/rerun", params=params, headers={"X-Role": "scoped-owner"}
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


@pytest.mark.parametrize("run_status", ["planned", "generated", "evaluated"])
def test_inflight_run_refuses_rerun(
    scoped_tasks: tuple[TestClient, Path], run_status: str
) -> None:
    client, root = scoped_tasks
    status = root / "workspaces/uuid-a/specs/same-spec/status.json"
    status.write_text(json.dumps({"status": run_status, "dismissed": True}))
    assert (
        client.post(
            "/api/tfactory/tasks/same-spec/rerun", params={"project_id": "uuid-a"}
        ).status_code
        == 409
    )
    assert json.loads(status.read_text())["status"] == run_status


def test_concurrent_rerun_schedules_once(
    scoped_tasks: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root = scoped_tasks
    scheduled: list[dict[str, Any]] = []

    def schedule(**kwargs: Any) -> object:
        scheduled.append(kwargs)
        return object()

    monkeypatch.setattr("agents.planner.schedule_planner", schedule)
    url = "/api/tfactory/tasks/same-spec/rerun?project_id=uuid-a"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(url), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(scheduled) == 1
    status = json.loads(
        (root / "workspaces/uuid-a/specs/same-spec/status.json").read_text()
    )
    assert status["rerun_count"] == 1
