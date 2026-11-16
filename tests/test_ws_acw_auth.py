"""WebSocket ``acw_`` API key authentication tests (#312)."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.database.models import (  # noqa: E402
    ApiKey,
    Base,
    Organization,
    OrgMember,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

OWNED_API_FULL_RAW = "acw_ws_owned_api_full_token"
MCP_READ_ONLY_RAW = "acw_ws_mcp_read_only_token"
EXPIRED_RAW = "acw_ws_expired_token"
OWNERLESS_RAW = "acw_ws_ownerless_token"
UNKNOWN_RAW = "acw_ws_unknown_token"
LEGACY_TOKEN = "legacy-ws-token-for-test"


def _digest(raw: str) -> str:
    return f"{raw[:8]}${hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _make_ws(token: str | None) -> MagicMock:
    ws = MagicMock()
    headers: dict[str, str] = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    ws.headers = headers
    ws.query_params = {}
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def ws_auth_env(tmp_path, monkeypatch):
    """Set up an isolated SQLite DB with seeded API keys and auth enabled."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ws_acw.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as s:
            s.add(User(id="user-1", email="u1@example.test", password_hash=""))
            await s.flush()
            s.add(
                Organization(id="org-1", name="Org 1", slug="org-1", owner_id="user-1")
            )
            await s.flush()
            s.add(OrgMember(org_id="org-1", user_id="user-1", role="member"))
            s.add_all(
                [
                    ApiKey(
                        id="key-full",
                        user_id="user-1",
                        org_id="org-1",
                        key_hash=_digest(OWNED_API_FULL_RAW),
                        name="owned-full",
                        scopes="api:full,mcp:read",
                    ),
                    ApiKey(
                        id="key-read",
                        user_id="user-1",
                        org_id="org-1",
                        key_hash=_digest(MCP_READ_ONLY_RAW),
                        name="mcp-read-only",
                        scopes="mcp:read",
                    ),
                    ApiKey(
                        id="key-expired",
                        user_id="user-1",
                        org_id="org-1",
                        key_hash=_digest(EXPIRED_RAW),
                        name="expired",
                        scopes="api:full",
                        expires_at=datetime.now(UTC) - timedelta(hours=1),
                    ),
                    ApiKey(
                        id="key-ownerless",
                        user_id="",
                        org_id="org-1",
                        key_hash=_digest(OWNERLESS_RAW),
                        name="ownerless",
                        scopes="api:full",
                    ),
                ]
            )
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_local() as s:
            yield s

    import server.mcp_remote.auth as mcp_auth  # noqa: PLC0415
    from server import auth as auth_mod  # noqa: PLC0415
    from server.config import get_settings  # noqa: PLC0415

    monkeypatch.setattr(mcp_auth, "get_db", _get_db)
    settings = get_settings()
    monkeypatch.setattr(settings, "DISABLE_AUTH", False, raising=False)
    monkeypatch.setattr(settings, "API_TOKEN", LEGACY_TOKEN, raising=False)

    yield auth_mod, mcp_auth, settings
    asyncio.run(engine.dispose())


def test_owned_api_full_key_returns_principal(ws_auth_env):
    auth_mod, _, _ = ws_auth_env
    ws = _make_ws(OWNED_API_FULL_RAW)

    principal = asyncio.run(auth_mod.authenticate_websocket(ws))

    assert principal == {
        "id": "user-1",
        "email": None,
        "role": "user",
        "api_key_id": "key-full",
        "org_id": "org-1",
    }
    ws.close.assert_not_awaited()


@pytest.mark.parametrize(
    "raw_token",
    [
        MCP_READ_ONLY_RAW,
        EXPIRED_RAW,
        OWNERLESS_RAW,
        UNKNOWN_RAW,
    ],
    ids=["mcp_read_only", "expired", "ownerless", "unknown"],
)
def test_invalid_or_underscoped_acw_keys_close_with_4001(ws_auth_env, raw_token):
    auth_mod, _, _ = ws_auth_env
    ws = _make_ws(raw_token)

    with pytest.raises(auth_mod.WebSocketAuthError):
        asyncio.run(auth_mod.authenticate_websocket(ws))

    ws.close.assert_awaited_once_with(code=4001, reason="Unauthorized")


def test_raising_validator_closes_with_4001(ws_auth_env, monkeypatch):
    auth_mod, mcp_auth, _ = ws_auth_env

    async def _boom(_header: str | None):
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(mcp_auth, "authenticate", _boom)
    ws = _make_ws(OWNED_API_FULL_RAW)

    with pytest.raises(auth_mod.WebSocketAuthError):
        asyncio.run(auth_mod.authenticate_websocket(ws))

    ws.close.assert_awaited_once_with(code=4001, reason="Unauthorized")


def test_jwt_and_legacy_paths_unchanged(ws_auth_env):
    auth_mod, _, settings = ws_auth_env

    jwt_token = jwt.encode(
        {
            "sub": "jwt-user-1",
            "email": "jwt@example.test",
            "role": "admin",
            "type": "access",
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    ws_jwt = _make_ws(jwt_token)
    jwt_principal = asyncio.run(auth_mod.authenticate_websocket(ws_jwt))
    assert jwt_principal == {
        "id": "jwt-user-1",
        "email": "jwt@example.test",
        "role": "admin",
    }
    ws_jwt.close.assert_not_awaited()

    ws_legacy = _make_ws(LEGACY_TOKEN)
    legacy_principal = asyncio.run(auth_mod.authenticate_websocket(ws_legacy))
    assert legacy_principal is None
    ws_legacy.close.assert_not_awaited()
