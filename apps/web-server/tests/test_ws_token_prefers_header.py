"""WebSocket auth must prefer the Authorization header over ?token= (#555).

Passing a bearer token in the URL leaks it into proxy logs, access logs and
browser history. The previous order here read the query param FIRST and only
fell back to the header, so a correctly-behaving client that sent a header
still had its token written to every log in the path.

The discriminating assertion is `test_header_wins_when_both_present`: the old
code passes every other test in this file, because it did read the header — just
second. Only a request carrying BOTH distinguishes the fix from the bug.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server import auth as auth_mod  # noqa: E402


def _ws(
    header: str | None = None,
    query: str | None = None,
    cookie: str | None = None,
    origin: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        headers={
            **({"authorization": header} if header else {}),
            **({"origin": origin} if origin else {}),
            "host": "app.example.test",
        },
        query_params={"token": query} if query else {},
        cookies={"access_token": cookie} if cookie else {},
        url=SimpleNamespace(scheme="wss"),
        close=AsyncMock(),
    )


def test_header_is_used() -> None:
    assert auth_mod._ws_extract_token(_ws(header="Bearer hdr-token")) == "hdr-token"


def test_query_param_still_works() -> None:
    assert auth_mod._ws_extract_token(_ws(query="qs-token")) == "qs-token"


def test_header_wins_when_both_present() -> None:
    # THE DISCRIMINATING CASE. The old order returned the query token here, so a
    # client doing the right thing still leaked its token into the URL path.
    ws = _ws(header="Bearer hdr-token", query="qs-token")
    assert auth_mod._ws_extract_token(ws) == "hdr-token"


def test_token_order_with_cookie() -> None:
    ws = _ws(header="Bearer hdr-token", query="qs-token", cookie="cookie-token")
    assert auth_mod._ws_extract_token(ws) == "hdr-token"
    ws = _ws(query="qs-token", cookie="cookie-token")
    assert auth_mod._ws_extract_token(ws) == "qs-token"


@pytest.mark.parametrize(
    "origin", ["https://app.example.test", "https://allowed.example.test"]
)
def test_cookie_with_accepted_origin_authenticates(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: SimpleNamespace(
            DISABLE_AUTH=False, CORS_ORIGINS=["https://allowed.example.test"]
        ),
    )
    monkeypatch.setattr(
        auth_mod,
        "_try_decode_jwt",
        lambda token: {"sub": "user-1"} if token == "cookie-token" else None,
    )
    assert asyncio.run(
        auth_mod.authenticate_websocket(_ws(cookie="cookie-token", origin=origin))
    ) == {"id": "user-1", "email": None, "role": "user"}


@pytest.mark.parametrize(
    "origin", [None, "https://foreign.example.test", "http://app.example.test"]
)
def test_cookie_with_untrusted_origin_closes_4001(
    monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: SimpleNamespace(
            DISABLE_AUTH=False, CORS_ORIGINS=["https://allowed.example.test"]
        ),
    )
    ws = _ws(cookie="cookie-token", origin=origin)
    with pytest.raises(auth_mod.WebSocketAuthError, match="No token provided"):
        asyncio.run(auth_mod.authenticate_websocket(ws))
    ws.close.assert_awaited_once_with(code=4001, reason="Unauthorized")


def test_header_auth_ignores_foreign_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth_mod, "get_settings", lambda: SimpleNamespace(DISABLE_AUTH=False)
    )
    monkeypatch.setattr(
        auth_mod,
        "_try_decode_jwt",
        lambda token: {"sub": "user-1"} if token == "hdr-token" else None,
    )
    ws = _ws(
        header="Bearer hdr-token",
        cookie="cookie-token",
        origin="https://foreign.example.test",
    )
    assert asyncio.run(auth_mod.authenticate_websocket(ws))["id"] == "user-1"
    ws.close.assert_not_awaited()


def test_no_token_returns_none() -> None:
    assert auth_mod._ws_extract_token(_ws()) is None


def test_non_bearer_header_falls_through_to_query() -> None:
    ws = _ws(header="Basic abc", query="qs-token")
    assert auth_mod._ws_extract_token(ws) == "qs-token"


def test_query_use_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    auth_mod._warn_ws_query_token_once.cache_clear()
    sentinel = "secret-sentinel-never-log"
    with caplog.at_level("WARNING"):
        auth_mod._ws_extract_token(_ws(query=sentinel))
        auth_mod._ws_extract_token(_ws(query="b"))
    warnings = [r for r in caplog.records if "DEPRECATED" in r.getMessage()]
    assert len(warnings) == 1, (
        "the deprecation notice must be emitted once, not per call"
    )
    assert sentinel not in caplog.text


def test_header_use_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    auth_mod._warn_ws_query_token_once.cache_clear()
    with caplog.at_level("WARNING"):
        auth_mod._ws_extract_token(_ws(header="Bearer hdr"))
    assert not [r for r in caplog.records if "DEPRECATED" in r.getMessage()]
