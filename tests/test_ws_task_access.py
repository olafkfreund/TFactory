"""Task WebSocket project checks through both endpoint handlers."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_ROOT = Path(__file__).resolve().parent.parent
for directory in ("web-server", "backend"):
    path = str(_ROOT / "apps" / directory)
    if path not in sys.path:
        sys.path.insert(0, path)

from server.websockets import logs, progress, task_access  # noqa: E402


@pytest.fixture(params=[("logs", logs), ("progress", progress)])
def endpoint(monkeypatch, request):
    name, module = request.param
    app = FastAPI()
    app.include_router(module.router)
    principal = {"id": "user"}
    setting = SimpleNamespace(RBAC_ENFORCE_TASK_ROUTES=True)
    authorize = AsyncMock()
    validator = AsyncMock(
        return_value={"id": "user", "api_key_id": "key", "org_id": "A"}
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class Service:
        def register_log_callback(self, *_):
            return lambda: None

        def register_progress_callback(self, *_):
            return lambda: None

        def is_running(self, *_):
            return False

    monkeypatch.setattr(
        task_access, "authenticate_websocket", AsyncMock(return_value=principal)
    )
    monkeypatch.setattr(task_access, "require_project_role", authorize)
    monkeypatch.setattr(task_access, "async_session_factory", Session)
    monkeypatch.setattr(task_access, "_ws_acw_principal", validator)
    monkeypatch.setattr(module, "get_agent_service", Service)
    return app, name, principal, setting, authorize, validator


def _path(name, task_id="project:spec"):
    return f"/ws/tasks/{task_id}/{name}"


def _connect(client, name, task_id="project:spec", token="jwt"):
    return client.websocket_connect(
        _path(name, task_id), headers={"authorization": f"Bearer {token}"}
    )


def _denied(app, name, task_id="project:spec", token="jwt", code=4003):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with _connect(client, name, task_id, token):
                pass
    assert exc.value.code == code


def test_member_and_service_connect(endpoint):
    app, name, principal, _, authorize, _ = endpoint
    with TestClient(app) as client, _connect(client, name) as ws:
        assert ws.receive_json()["type"] == "connected"
    assert authorize.await_args.args[1] == "project"
    principal.clear()  # Auth stub now represents the legacy/service path below.
    task_access.authenticate_websocket.return_value = None
    _denied(app, name, "bare")


@pytest.mark.parametrize("task_id", ["project", ":spec", "project:"])
def test_bad_task_id_denied_even_in_shadow(endpoint, task_id):
    app, name, _, setting, authorize, _ = endpoint
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    _denied(app, name, task_id)
    authorize.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [PermissionError("not member"), RuntimeError("db unavailable")]
)
@pytest.mark.parametrize("strict", [False, True])
def test_jwt_flag_controls_denial_and_errors(endpoint, failure, strict, caplog):
    app, name, _, setting, authorize, _ = endpoint
    setting.RBAC_ENFORCE_TASK_ROUTES = strict
    authorize.side_effect = failure
    _denied(app, name)


@pytest.mark.parametrize(
    "failure", [PermissionError("other org"), RuntimeError("registry unavailable")]
)
def test_key_always_strict(endpoint, failure):
    app, name, principal, setting, authorize, _ = endpoint
    principal.update(api_key_id="key", org_id="B")
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    authorize.side_effect = failure
    _denied(app, name, token="acw_key")


def test_unknown_project_denied(endpoint):
    app, name, _, _, authorize, _ = endpoint
    authorize.side_effect = HTTPException(status_code=404, detail="Project not found")
    _denied(app, name)


@pytest.mark.parametrize(
    "outcome", [None, RuntimeError("validator failed"), "removed", "db failed"]
)
def test_key_recheck_closes_on_revocation_or_access_failure(
    endpoint, monkeypatch, outcome
):
    app, name, principal, _, authorize, validator = endpoint
    principal.update(api_key_id="key", org_id="A")
    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)
    if outcome == "removed":
        authorize.side_effect = [None, PermissionError("membership removed")]
    elif outcome == "db failed":
        authorize.side_effect = [None, RuntimeError("database unavailable")]
    else:
        validator.side_effect = [outcome] if isinstance(outcome, Exception) else None
        validator.return_value = outcome
    with TestClient(app) as client, _connect(client, name, token="acw_key") as ws:
        assert ws.receive_json()["type"] == "connected"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4001
