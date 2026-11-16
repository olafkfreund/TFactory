"""All-task WebSocket subscriptions respect project access."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_ROOT = Path(__file__).resolve().parent.parent
for directory in ("web-server", "backend"):
    path = str(_ROOT / "apps" / directory)
    if path not in sys.path:
        sys.path.insert(0, path)

from server.services.agent_service import TaskLog, TaskPhase, TaskProgress  # noqa: E402
from server.websockets import logs, progress, task_access  # noqa: E402


@pytest.fixture(params=[("logs", logs), ("progress", progress)])
def endpoint(monkeypatch, request):
    name, module = request.param
    app = FastAPI()
    app.include_router(module.router)
    principal = {"id": "user"}
    setting = SimpleNamespace(RBAC_ENFORCE_TASK_ROUTES=True)
    authorize = AsyncMock()
    validator = AsyncMock(return_value={"id": "user", "api_key_id": "key"})

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class Service:
        def __init__(self):
            self.tasks = ["own:one", "other:two", "bare"]
            self.callbacks = {}

        def get_running_tasks(self):
            return self.tasks

        def register_log_callback(self, task_id, callback):
            return self._register(task_id, callback)

        def register_progress_callback(self, task_id, callback):
            return self._register(task_id, callback)

        def _register(self, task_id, callback):
            self.callbacks[task_id] = callback
            return lambda: self.callbacks.pop(task_id, None)

    service = Service()

    async def check(_, project_id, __):
        if project_id == "other":
            raise PermissionError("other org")

    authorize.side_effect = check
    monkeypatch.setattr(
        task_access, "authenticate_websocket", AsyncMock(return_value=principal)
    )
    monkeypatch.setattr(task_access, "authorize_project_for_user", authorize)
    monkeypatch.setattr(task_access, "async_session_factory", Session)
    monkeypatch.setattr(task_access, "_ws_acw_principal", validator)
    monkeypatch.setattr(task_access, "get_settings", lambda: setting)
    monkeypatch.setattr(module, "get_agent_service", lambda: service)
    return app, name, principal, setting, service, authorize, validator


def _connect(client, name, token="jwt"):
    return client.websocket_connect(
        f"/ws/{name}", headers={"authorization": f"Bearer {token}"}
    )


def test_member_sees_only_own_project(endpoint):
    app, name, _, _, service, authorize, _ = endpoint
    with TestClient(app) as client, _connect(client, name) as ws:
        connected = ws.receive_json()
        assert connected["type"] == "connected"
        if name == "progress":
            assert connected["running_tasks"] == ["own:one"]
        assert set(service.callbacks) == {"own:one"}
    assert {call.args[1] for call in authorize.await_args_list} == {"own", "other"}


@pytest.mark.parametrize("identity", [None, {"id": "service", "is_service": True}])
def test_legacy_and_service_see_all(endpoint, identity):
    app, name, _, _, service, authorize, _ = endpoint
    task_access.authenticate_websocket.return_value = identity
    with TestClient(app) as client, _connect(client, name) as ws:
        connected = ws.receive_json()
        if name == "progress":
            assert connected["running_tasks"] == service.tasks
        assert set(service.callbacks) == set(service.tasks)
    authorize.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [PermissionError("denied"), RuntimeError("db down")]
)
@pytest.mark.parametrize("strict", [False, True])
def test_jwt_shadow_and_strict(endpoint, failure, strict, caplog):
    app, name, _, setting, service, authorize, _ = endpoint
    setting.RBAC_ENFORCE_TASK_ROUTES = strict
    authorize.side_effect = failure
    with TestClient(app) as client, _connect(client, name) as ws:
        ws.receive_json()
        assert set(service.callbacks) == (set() if strict else {"own:one", "other:two"})
    if not strict:
        assert "Task WS access would deny" in caplog.text


def test_key_always_strict_and_revocation(endpoint, monkeypatch):
    app, name, principal, setting, service, _, validator = endpoint
    principal["api_key_id"] = "key"
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)
    validator.return_value = None
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with _connect(client, name, "acw_key") as ws:
                ws.receive_json()
    assert exc.value.code == 4001
    assert not service.callbacks


def test_queued_denied_message_is_dropped(endpoint):
    app, name, _, _, service, _, _ = endpoint
    original_register = service._register

    def register(task_id, callback):
        unregister = original_register(task_id, callback)
        if task_id == "own:one":
            message = (
                TaskLog("other:two", "secret")
                if name == "logs"
                else TaskProgress("other:two", TaskPhase.CODING, "secret")
            )
            asyncio.create_task(callback(message))  # noqa: RUF006
        return unregister

    service._register = register
    with TestClient(app) as client, _connect(client, name) as ws:
        ws.receive_json()
        assert ws.receive_json()["type"] == "heartbeat"


@pytest.mark.asyncio
async def test_refresh_deadline_drops_newly_denied_message(endpoint, monkeypatch):
    _, name, _, _, service, authorize, _ = endpoint
    module = logs if name == "logs" else progress
    clock = SimpleNamespace(now=0.0)
    sent = []

    class Socket:
        async def accept(self):
            pass

        async def send_json(self, message):
            sent.append(message)
            if message["type"] == "heartbeat":
                raise WebSocketDisconnect()

    async def check(_, project_id, __):
        if project_id == "other" or clock.now >= 5.0:
            raise PermissionError("membership removed")

    authorize.side_effect = check
    monkeypatch.setattr(task_access, "_ws_extract_token", lambda _: None)
    items = iter((4.9, 5.1, 10.1))

    async def wait_for(pending, timeout):
        pending.close()
        clock.now = next(items)
        if clock.now > 10.0:
            raise TimeoutError
        if name == "logs":
            return TaskLog("own:one", "update")
        return TaskProgress("own:one", TaskPhase.CODING, "update")

    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            Queue=asyncio.Queue,
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            wait_for=wait_for,
        ),
    )
    handler = (
        module.all_logs_websocket if name == "logs" else module.all_progress_websocket
    )
    await handler(Socket())
    assert [message["type"] for message in sent] == [
        "connected",
        name[:-1] if name == "logs" else "progress",
        "heartbeat",
    ]
    assert not service.callbacks
    if name == "progress":
        assert sent[-1]["running_tasks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id,delivered", [("own:one", True), ("other:two", False)])
async def test_final_event_after_task_stops(endpoint, monkeypatch, task_id, delivered):
    _, name, _, _, service, _, _ = endpoint
    module = logs if name == "logs" else progress
    event_type = "log" if name == "logs" else "progress"
    clock = SimpleNamespace(now=0.0)
    sent = []

    class Socket:
        async def accept(self):
            pass

        async def send_json(self, message):
            sent.append(message)
            if message["type"] == "heartbeat":
                raise WebSocketDisconnect()

    monkeypatch.setattr(task_access, "_ws_extract_token", lambda _: None)
    calls = 0

    async def wait_for(pending, timeout):
        nonlocal calls
        pending.close()
        calls += 1
        if calls == 1:
            clock.now = 5.1
            service.tasks.remove(task_id)
            if name == "logs":
                return TaskLog(task_id, "final")
            return TaskProgress(task_id, TaskPhase.CODING, "final")
        clock.now = 10.2
        raise TimeoutError

    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            Queue=asyncio.Queue,
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            wait_for=wait_for,
        ),
    )
    handler = (
        module.all_logs_websocket if name == "logs" else module.all_progress_websocket
    )
    await handler(Socket())
    events = [message for message in sent if message["type"] == event_type]
    assert bool(events) is delivered
    if delivered:
        assert events[0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_event_during_slow_lookup_is_not_lost(endpoint, monkeypatch):
    _, name, _, _, service, _, _ = endpoint
    module = logs if name == "logs" else progress
    event_type = "log" if name == "logs" else "progress"
    service.tasks = ["own:one"]
    clock = SimpleNamespace(now=0.0)
    sent = []

    class Socket:
        async def accept(self):
            pass

        async def send_json(self, message):
            sent.append(message)
            if message["type"] == event_type:
                raise WebSocketDisconnect()

    monkeypatch.setattr(task_access, "_ws_extract_token", lambda _: None)
    original_readable = task_access.AllTaskSocketAccess.readable
    lookups = 0

    async def slow_readable(self, task_ids):
        nonlocal lookups
        lookups += 1
        if lookups == 2:
            await asyncio.sleep(0)
            callback = service.callbacks.get("own:one")
            assert callback is not None
            event = (
                TaskLog("own:one", "during lookup")
                if name == "logs"
                else TaskProgress("own:one", TaskPhase.CODING, "during lookup")
            )
            await callback(event)
        return await original_readable(self, task_ids)

    monkeypatch.setattr(task_access.AllTaskSocketAccess, "readable", slow_readable)
    calls = 0

    async def wait_for(pending, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            pending.close()
            clock.now = 5.1
            raise TimeoutError
        return await pending

    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            Queue=asyncio.Queue,
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            wait_for=wait_for,
        ),
    )
    handler = (
        module.all_logs_websocket if name == "logs" else module.all_progress_websocket
    )
    await handler(Socket())
    assert [message["type"] for message in sent] == [
        "connected",
        "heartbeat",
        event_type,
    ]
    assert not service.callbacks
