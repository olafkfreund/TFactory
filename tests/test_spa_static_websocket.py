"""Regression test for #670 — SPA StaticFiles must not crash on websocket scopes.

The SPA shell is served by ``SPAStaticFiles`` mounted catch-all at ``/``. A
Starlette ``Mount`` matches ``websocket`` scopes as well as ``http``, so a
websocket that matches no ``/ws/*`` route falls through to this mount. Stock
``StaticFiles.__call__`` asserts ``scope["type"] == "http"`` and raised an
AssertionError on every such connection, flooding logs and breaking live
task-status streaming. The guard rejects non-HTTP scopes cleanly instead.
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
from server.main import SPAStaticFiles  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402


def _app(static_dir: Path) -> FastAPI:
    (static_dir / "index.html").write_text("<html></html>")
    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="static")
    return app


def test_websocket_fallthrough_does_not_assertionerror(tmp_path):
    """A ws connection that falls through to the SPA mount closes cleanly."""
    client = TestClient(_app(tmp_path))
    # Before the fix this raised AssertionError('assert scope["type"] == "http"')
    # inside the ASGI app. Now the mount closes the socket, which the test
    # client surfaces as a clean WebSocketDisconnect during the handshake.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/does-not-match") as ws:
            ws.receive_text()


def test_http_still_served(tmp_path):
    """The HTTP path is unaffected: the SPA shell is still served."""
    client = TestClient(_app(tmp_path))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-cache, must-revalidate"


# ─── #878: history fallback for client-side routes ──────────────────────


@pytest.mark.parametrize("route", ["/login", "/console/portal-ui/i886-e2e"])
def test_client_routes_serve_the_shell(tmp_path, route):
    """A direct GET of a React Router route returns index.html, not a 404.

    Before #878 every client route answered with FastAPI's JSON 404, so a task
    link could not be shared, bookmarked, or reloaded.
    """
    client = TestClient(_app(tmp_path))
    resp = client.get(route)
    assert resp.status_code == 200
    assert resp.text == "<html></html>"
    assert resp.headers["Cache-Control"] == "no-cache, must-revalidate"


@pytest.mark.parametrize(
    "route",
    [
        "/api/tfactory/tasks/nope",  # API misses stay machine-readable
        "/ws/nope",
        "/assets/index-deadbeef.js",  # a missing bundle must fail loudly
        "/favicon.ico",  # any file-looking path
    ],
)
def test_server_owned_paths_keep_their_404(tmp_path, route):
    """The fallback must not swallow a real miss.

    Answering a missing hashed asset with HTML turns a broken deploy into a
    blank page with no error, and an /api miss that returns the shell is
    unparseable to every client.
    """
    client = TestClient(_app(tmp_path))
    assert client.get(route).status_code == 404
