"""Tests for user-minted ``acw_`` API-key auth on the REST surface (#305).

Two layers are covered:

1. ``TokenAuthMiddleware`` Strategy 3 — an ``acw_`` key authenticates on
   ``/api/*`` only when it carries the ``api:full`` scope; narrower keys are
   rejected with 403, preserving the MCP-only guard.
2. ``mcp_remote.auth._lookup_by_digest`` — rejects expired keys, stamps
   ``last_used_at``, and no longer references a non-existent ``is_active``
   column (the latent AttributeError bug this issue fixes).

No real database or HTTP server is touched: the middleware test monkeypatches
the key validator, and the lookup test drives a stub async session.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server import auth as auth_mod  # noqa: E402
from server.auth import TokenAuthMiddleware  # noqa: E402
from server.mcp_remote import auth as mcp_auth  # noqa: E402
from server.mcp_remote.auth import (  # noqa: E402
    REST_API_SCOPE,
    AuthenticatedKey,
    MCPAuthError,
    _lookup_by_digest,
)


# ---------------------------------------------------------------------------
# Middleware Strategy 3
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """App with TokenAuthMiddleware + a trivial protected route."""
    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: SimpleNamespace(
            DISABLE_AUTH=False,
            API_TOKEN="legacy-secret",
            JWT_SECRET="test-secret",
            JWT_ALGORITHM="HS256",
        ),
    )

    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware)

    @app.get("/api/ping")
    async def ping():  # noqa: ANN202 - test stub
        return {"ok": True}

    return TestClient(app)


def _stub_authenticate(monkeypatch, *, scopes=None, raises=False):
    async def _fake(authorization_header: str):
        if raises:
            raise MCPAuthError("Invalid API key")
        return AuthenticatedKey(
            key_id="key-1",
            scopes=frozenset(scopes or ()),
            user_id="user-1",
            org_id="org-1",
        )

    monkeypatch.setattr(mcp_auth, "authenticate", _fake)


def test_missing_token_rejected(client):
    assert client.get("/api/ping").status_code == 401


def test_acw_key_with_api_full_scope_allowed(client, monkeypatch):
    _stub_authenticate(monkeypatch, scopes={REST_API_SCOPE})
    r = client.get("/api/ping", headers={"Authorization": "Bearer acw_validkey"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_acw_key_without_api_full_scope_forbidden(client, monkeypatch):
    # An mcp:read-only key must NOT reach the REST surface (the guard).
    _stub_authenticate(monkeypatch, scopes={"mcp:read"})
    r = client.get("/api/ping", headers={"Authorization": "Bearer acw_mcponly"})
    assert r.status_code == 403
    assert REST_API_SCOPE in r.json()["error"]


def test_invalid_acw_key_forbidden(client, monkeypatch):
    _stub_authenticate(monkeypatch, raises=True)
    r = client.get("/api/ping", headers={"Authorization": "Bearer acw_bogus"})
    assert r.status_code == 403


def test_legacy_token_still_works(client):
    r = client.get("/api/ping", headers={"Authorization": "Bearer legacy-secret"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# _lookup_by_digest: expiry, last_used stamping, no is_active reference
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """Minimal async session stub: one execute() result + commit/rollback."""

    def __init__(self, row):
        self._row = row
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        return _FakeResult(self._row)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _row(*, expires_at=None, scopes="api:full"):
    # Deliberately NO is_active attribute — accessing one would raise
    # AttributeError, which is exactly the bug #305 removes.
    return SimpleNamespace(
        id="key-1",
        user_id="user-1",
        org_id="org-1",
        key_hash="acw_test$deadbeef",
        scopes=scopes,
        expires_at=expires_at,
        last_used_at=None,
    )


@pytest.mark.asyncio
async def test_lookup_valid_key_stamps_last_used():
    row = _row()
    session = _FakeSession(row)
    key = await _lookup_by_digest(session, "deadbeef")
    assert key.user_id == "user-1"
    assert key.has_scope(REST_API_SCOPE)
    assert row.last_used_at is not None  # stamped
    assert session.committed is True


@pytest.mark.asyncio
async def test_lookup_unknown_key_raises():
    session = _FakeSession(None)
    with pytest.raises(MCPAuthError, match="Invalid API key"):
        await _lookup_by_digest(session, "nope")


@pytest.mark.asyncio
async def test_lookup_expired_key_raises():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    session = _FakeSession(_row(expires_at=past))
    with pytest.raises(MCPAuthError, match="expired"):
        await _lookup_by_digest(session, "deadbeef")


@pytest.mark.asyncio
async def test_lookup_unexpired_key_ok():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    session = _FakeSession(_row(expires_at=future))
    key = await _lookup_by_digest(session, "deadbeef")
    assert key.key_id == "key-1"


@pytest.mark.asyncio
async def test_lookup_naive_expiry_treated_as_utc():
    # SQLite may hand back a naive datetime; it must compare as UTC, not crash.
    naive_past = datetime.utcnow() - timedelta(days=1)
    session = _FakeSession(_row(expires_at=naive_past))
    with pytest.raises(MCPAuthError, match="expired"):
        await _lookup_by_digest(session, "deadbeef")
