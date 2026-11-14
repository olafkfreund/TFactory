"""Organization routing and confinement tests for ``/ws/events`` (#3679)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "apps" / "web-server") not in sys.path:
    sys.path.insert(0, str(_ROOT / "apps" / "web-server"))
if str(_ROOT / "apps" / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "apps" / "backend"))

from server import auth  # noqa: E402
from server.database import engine  # noqa: E402
from server.mcp_remote import auth as mcp_auth  # noqa: E402
from server.routes import (  # noqa: E402
    cli_accounts,
    github_auth,
    project_authz,
)
from server.routes import projects as projects_route  # noqa: E402
from server.services import notification_service as notification_module  # noqa: E402
from server.services.agent_service import TaskLogWriter, TaskPhase  # noqa: E402
from server.services.notification_service import NotificationService  # noqa: E402
from server.websockets import events  # noqa: E402

_real_ws_acw_principal = auth._ws_acw_principal


@pytest.fixture
def socket_app(monkeypatch):
    events._clients.clear()
    events.active_connections.clear()
    events._org_cache.clear()

    principals = {
        "jwt-a": {"id": "a-only"},
        "jwt-owner": {"id": "owner"},
        "jwt-b": {"id": "other"},
        "acw_a": {"id": "owner", "org_id": "A", "api_key_id": "key"},
        "shared": None,
    }
    memberships = {
        "owner": [("A",), ("B",)],
        "a-only": [("A",)],
        "other": [("B",)],
    }
    setting = SimpleNamespace(RBAC_ENFORCE_TASK_ROUTES=True, DISABLE_AUTH=False)
    sent: dict[str, list[dict]] = {}

    async def authenticate(ws):
        return principals[events._ws_extract_token(ws)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, stmt):
            uid = next(iter(stmt.compile().params.values()))
            return SimpleNamespace(all=lambda: memberships[uid])

    original_send = WebSocket.send_text

    async def record_send(ws, message):
        token = (
            dict(ws.scope["headers"])
            .get(b"authorization", b"")
            .decode()
            .removeprefix("Bearer ")
        )
        sent.setdefault(token, []).append(json.loads(message))
        await original_send(ws, message)

    monkeypatch.setattr(WebSocket, "send_text", record_send)
    monkeypatch.setattr(engine, "async_session_factory", Session)
    monkeypatch.setattr(events, "authenticate_websocket", authenticate)
    monkeypatch.setattr(events, "get_settings", lambda: setting)
    monkeypatch.setattr(project_authz, "get_settings", lambda: setting)
    monkeypatch.setattr(
        events,
        "load_projects",
        lambda: {
            "p-a": {"org_id": "A"},
            "p-b": {"org_id": "B"},
            "p-none": {},
        },
    )

    app = FastAPI()
    app.include_router(events.router)
    yield app, setting, sent

    events._clients.clear()
    events.active_connections.clear()
    events._org_cache.clear()


def _connect(client: TestClient, token: str):
    return client.websocket_connect(
        "/ws/events", headers={"authorization": f"Bearer {token}"}
    )


def _emit(client: TestClient, event_type: str, payload: dict):
    client.portal.call(events.broadcast_event, event_type, payload)


def test_project_events_reach_only_their_org(socket_app):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "jwt-a") as a,
        _connect(client, "jwt-b") as b,
        _connect(client, "shared") as service,
    ):
        _emit(client, "task:status", {"taskId": "p-b:001", "status": "done"})
        assert b.receive_json()["payload"]["status"] == "done"
        assert service.receive_json()["payload"]["status"] == "done"
        assert sent.get("jwt-a", []) == []

        _emit(client, "task:status", {"taskId": "p-a:001", "status": "ready"})
        assert a.receive_json()["payload"]["status"] == "ready"
        assert service.receive_json()["payload"]["status"] == "ready"
        assert len(sent["jwt-b"]) == 1


def test_service_and_bound_key_narrowing_and_widening(socket_app):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "shared") as service,
    ):
        key_client = next(c for c in events._clients.values() if c.key_org_id)
        assert key_client.key_org_id == "A"
        assert key_client.org_ids == {"A"}

        events.update_client_orgs("owner", {"A", "B"})
        _emit(client, "b", {"projectId": "p-b"})
        assert service.receive_json()["type"] == "b"
        assert sent.get("acw_a", []) == []

        _emit(client, "a", {"projectId": "p-a"})
        assert key.receive_json()["type"] == "a"
        assert [m["type"] for m in sent["acw_a"]] == ["a"]


def test_real_task_status_emitter_routes_by_task_project(socket_app):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "jwt-a") as a,
        _connect(client, "jwt-b") as b,
    ):
        client.portal.call(events.emit_task_status, "p-b:001-x", "running")
        received = b.receive_json()
        assert received["payload"]["taskId"] == "p-b:001-x"
        assert received["payload"]["status"] == "running"
        assert sent.get("jwt-a", []) == []

        _emit(client, "sentinel", {"projectId": "p-a"})
        assert a.receive_json()["type"] == "sentinel"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"taskId": "001-bare"},
        {"projectId": "missing"},
        {"projectId": "p-none"},
    ],
)
def test_unresolved_and_no_org_events_are_service_only(socket_app, payload):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-a") as jwt,
        _connect(client, "shared") as service,
    ):
        _emit(client, "unresolved", payload)
        assert service.receive_json()["type"] == "unresolved"
        assert sent.get("acw_a", []) == []
        assert sent.get("jwt-a", []) == []

        _emit(client, "known", {"projectId": "p-a"})
        assert key.receive_json()["type"] == "known"
        assert jwt.receive_json()["type"] == "known"


def test_registry_failure_is_service_only(socket_app, monkeypatch):
    app, _, sent = socket_app

    def fail():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(events, "load_projects", fail)
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-a"),
        _connect(client, "shared") as service,
    ):
        _emit(client, "failed", {"projectId": "p-a"})
        assert service.receive_json()["type"] == "failed"
        assert sent.get("acw_a", []) == []
        assert sent.get("jwt-a", []) == []

        client.portal.call(events.send_to_org, "A", "sentinel", {})
        assert key.receive_json()["type"] == "sentinel"


def test_user_events_require_matching_org_for_key(socket_app):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-owner") as jwt,
    ):
        client.portal.call(
            events.send_to_user, "owner", "cli-account-auth", {"success": True}
        )
        assert jwt.receive_json()["type"] == "cli-account-auth"
        assert sent.get("acw_a", []) == []

        client.portal.call(
            events.send_to_user,
            "owner",
            "notification:new",
            {"data": {"org_id": "B"}},
        )
        assert jwt.receive_json()["payload"]["data"]["org_id"] == "B"
        assert sent.get("acw_a", []) == []

        client.portal.call(
            events.send_to_user,
            "owner",
            "notification:new",
            {"data": {"org_id": "A"}},
        )
        assert key.receive_json()["payload"]["data"]["org_id"] == "A"
        assert [m["type"] for m in sent["acw_a"]] == ["notification:new"]

        # Membership gone (failed lookup or removal): the key socket gets nothing.
        events.update_client_orgs("owner", set())
        client.portal.call(
            events.send_to_user, "owner", "dropped", {"data": {"org_id": "A"}}
        )
        assert sent["jwt-owner"][-1]["type"] == "dropped"
        assert [m["type"] for m in sent["acw_a"]] == ["notification:new"]


def test_notify_org_overrides_untrusted_data(socket_app, monkeypatch):
    app, _, _ = socket_app
    service = NotificationService()
    monkeypatch.setattr(service, "_maybe_send_email", AsyncMock())
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-owner") as jwt,
    ):

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, _stmt):
                return SimpleNamespace(all=lambda: [("owner",)])

        monkeypatch.setattr(notification_module, "async_session_factory", Session)

        async def send():
            return await service.notify_org(
                org_id="A",
                type="test",
                title="T",
                message="M",
                data={"org_id": "B"},
            )

        client.portal.call(send)
        assert key.receive_json()["payload"]["data"]["org_id"] == "A"
        assert jwt.receive_json()["payload"]["data"]["org_id"] == "A"


def test_flag_tracks_rest_dependency_on_open_socket(socket_app, monkeypatch):
    app, setting, sent = socket_app
    monkeypatch.setattr(
        projects_route, "load_projects", lambda: {"p-b": {"org_id": "B"}}
    )
    monkeypatch.setattr(project_authz, "_audit_authz_denial", AsyncMock())
    db = SimpleNamespace(
        rollback=AsyncMock(),
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        ),
    )
    request = Request({"type": "http"})
    request.state.user = {"id": "a-only"}
    checker = project_authz.ProjectAccessChecker()
    with TestClient(app) as client, _connect(client, "jwt-a") as ws:
        setting.RBAC_ENFORCE_TASK_ROUTES = False
        # An explicit org send is members-only even in shadow mode.
        client.portal.call(events.send_to_org, "B", "explicit", {})
        _emit(client, "shadow", {"projectId": "p-b"})
        assert ws.receive_json()["type"] == "shadow"
        client.portal.call(checker.__call__, "p-b", request, db)

        setting.RBAC_ENFORCE_TASK_ROUTES = True
        _emit(client, "blocked", {"projectId": "p-b"})
        _emit(client, "allowed", {"projectId": "p-a"})
        assert ws.receive_json()["type"] == "allowed"
        assert [m["type"] for m in sent["jwt-a"]] == ["shadow", "allowed"]
        with pytest.raises(HTTPException) as exc:
            client.portal.call(checker.__call__, "p-b", request, db)
        assert exc.value.status_code == 403


def test_event_org_cache_expiry_and_reassignment(monkeypatch):
    events._org_cache.clear()
    clock = [0.0]
    projects: dict[str, dict[str, str]] = {"p": {}}
    calls: list[int] = []

    def load():
        calls.append(1)
        return projects

    monkeypatch.setattr(events.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(events, "load_projects", load)
    assert events._event_org({"projectId": "p"}) is None
    projects["p"]["org_id"] = "A"
    assert events._event_org({"projectId": "p"}) is None
    assert len(calls) == 1
    clock[0] = 31.0
    assert events._event_org({"projectId": "p"}) == "A"
    projects["p"]["org_id"] = "B"
    clock[0] = 62.0
    assert events._event_org({"projectId": "p"}) == "B"


@pytest.mark.parametrize(
    "second_outcome",
    [None, RuntimeError("validator unavailable")],
    ids=["revoked", "raising_validator"],
)
@pytest.mark.parametrize("org", ["A", None])
def test_key_revocation_closes_socket(socket_app, monkeypatch, second_outcome, org):
    app, _, _ = socket_app
    key = mcp_auth.AuthenticatedKey("key-id", frozenset({"api:full"}), "owner", org)
    monkeypatch.setattr(
        mcp_auth,
        "authenticate",
        AsyncMock(side_effect=[key, second_outcome]),
    )
    monkeypatch.setattr(auth, "_ws_acw_principal", _real_ws_acw_principal)
    monkeypatch.setattr(events, "_ws_acw_principal", _real_ws_acw_principal)
    monkeypatch.setattr(events, "_KEY_RECHECK_SECONDS", 0)

    async def authenticate(ws):
        return await events._ws_acw_principal(events._ws_extract_token(ws))

    monkeypatch.setattr(events, "authenticate_websocket", authenticate)
    with TestClient(app) as client, _connect(client, "acw_a") as ws:
        with pytest.raises(Exception) as exc:
            ws.receive_text()
        assert getattr(exc.value, "code", None) == 4001
    assert not events._clients


def test_task_log_writer_stream_carries_project_id_and_routes_by_org(
    socket_app, tmp_path
):
    app, _, sent = socket_app
    writer = TaskLogWriter(tmp_path, project_id="p-a")

    async def add_and_flush():
        writer.add_entry("001-x", TaskPhase.CODING, "text", "hello")
        await asyncio.sleep(0.05)

    with (
        TestClient(app) as client,
        _connect(client, "jwt-a") as a,
        _connect(client, "jwt-b"),
    ):
        client.portal.call(add_and_flush)
        msg = a.receive_json()
        assert msg["type"] == "task-logs:stream"
        assert msg["payload"]["projectId"] == "p-a"
        assert msg["payload"]["specId"] == "001-x"
        assert sent.get("jwt-b", []) == []


@pytest.mark.parametrize("event", ["cli-account-auth", "github:auth-complete"])
def test_org_less_login_events_follow_starting_principal(socket_app, event):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "jwt-owner") as owner_ws,
        _connect(client, "jwt-b"),
        _connect(client, "acw_a"),
        _connect(client, "shared") as service_ws,
    ):
        client.portal.call(
            events.emit_org_less, {"id": "owner"}, event, {"success": True}
        )
        assert owner_ws.receive_json()["type"] == event
        assert sent.get("jwt-b", []) == []
        assert sent.get("acw_a", []) == []
        assert sent.get("shared", []) == []

        client.portal.call(
            events.emit_org_less,
            {"id": "default", "is_service": True},
            event,
            {"success": True},
        )
        assert service_ws.receive_json()["type"] == event
        assert len(sent["jwt-owner"]) == 1
        assert sent.get("jwt-b", []) == []
        assert sent.get("acw_a", []) == []


def test_cli_and_github_emitters_route_by_principal(socket_app, monkeypatch):
    app, _, sent = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "jwt-owner") as owner_ws,
        _connect(client, "jwt-b"),
        _connect(client, "acw_a"),
        _connect(client, "shared") as service_ws,
    ):

        async def run_cli(principal):
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(
                cli_accounts._broadcast_cli_auth_event,
                "codex",
                True,
                loop,
                principal,
            )

        client.portal.call(run_cli, {"id": "owner"})
        assert owner_ws.receive_json()["type"] == "cli-account-auth"
        assert sent.get("shared", []) == []
        assert sent.get("jwt-b", []) == []
        assert sent.get("acw_a", []) == []

        client.portal.call(run_cli, {"id": "default", "is_service": True})
        assert service_ws.receive_json()["type"] == "cli-account-auth"
        assert len(sent["jwt-owner"]) == 1

        class FakeProc:
            returncode = 0

            async def wait(self):
                return 0

        client.portal.call(github_auth._monitor_gh_auth, FakeProc(), {"id": "owner"})
        assert owner_ws.receive_json()["type"] == "github:auth-complete"
        assert len(sent["shared"]) == 1
        assert sent.get("jwt-b", []) == []
        assert sent.get("acw_a", []) == []

        client.portal.call(
            github_auth._monitor_gh_auth,
            FakeProc(),
            {"id": "default", "is_service": True},
        )
        assert service_ws.receive_json()["type"] == "github:auth-complete"
        assert len(sent["jwt-owner"]) == 2
