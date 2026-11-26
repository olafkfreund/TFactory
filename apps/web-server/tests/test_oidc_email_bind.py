"""Check OIDC email binding and the callback's refusal response."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from server.database import Base, User
from server.database.engine import get_db
from server.database.models import OidcRefreshSession
from server.oidc.provisioning import OIDCBindRefused, jit_provision_user
from server.routes import oidc_routes
from server.routes.oidc_routes import OIDC_BIND_REFUSED_DETAIL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

EMAIL = "local@example.test"
SUB = "incoming-subject"
OTHER_SUB = "existing-subject"
LOCAL_HASH = "local-password-hash"


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


async def _seed_user(db: AsyncSession, sub: str | None = None) -> str:
    user = User(email=EMAIL, oidc_sub=sub, password_hash=LOCAL_HASH)
    db.add(user)
    await db.commit()
    return user.id


async def _assert_only_user(
    db: AsyncSession, user_id: str, sub: str | None, password_hash: str
) -> None:
    rows = await db.execute(
        select(User.id, User.email, User.oidc_sub, User.password_hash)
    )
    assert rows.tuples().all() == [(user_id, EMAIL, sub, password_hash)]


def test_verified_email_binds_local_account_and_clears_password() -> None:
    async def run() -> None:
        async with _session() as db:
            user_id = await _seed_user(db)
            user = await jit_provision_user(
                db, {"sub": SUB, "email": EMAIL, "email_verified": True}
            )
            assert user.id == user_id
            await _assert_only_user(db, user_id, SUB, "")

    asyncio.run(run())


@pytest.mark.parametrize(
    "claims",
    [{}, *({"email_verified": value} for value in (False, None, "true", "True", 1))],
    ids=["missing", "false", "null", "string-true", "string-True", "integer-one"],
)
def test_unverified_email_refuses_bind(claims: dict[str, object]) -> None:
    async def run() -> None:
        async with _session() as db:
            user_id = await _seed_user(db)
            with pytest.raises(OIDCBindRefused, match=r"^email not verified$"):
                await jit_provision_user(db, {"sub": SUB, "email": EMAIL, **claims})
            await _assert_only_user(db, user_id, None, LOCAL_HASH)

    asyncio.run(run())


def test_verified_email_cannot_rebind_existing_subject() -> None:
    async def run() -> None:
        async with _session() as db:
            user_id = await _seed_user(db, OTHER_SUB)
            with pytest.raises(OIDCBindRefused, match=r"^account already linked$"):
                await jit_provision_user(
                    db, {"sub": SUB, "email": EMAIL, "email_verified": True}
                )
            await _assert_only_user(db, user_id, OTHER_SUB, LOCAL_HASH)

    asyncio.run(run())


def test_subject_match_ignores_email_claim_changes() -> None:
    async def run() -> None:
        async with _session() as db:
            user_id = await _seed_user(db, SUB)
            user = await jit_provision_user(
                db, {"sub": SUB, "email": "changed@example.test"}
            )
            assert user.id == user_id
            await _assert_only_user(db, user_id, SUB, LOCAL_HASH)

    asyncio.run(run())


def test_new_account_does_not_require_verified_email() -> None:
    async def run() -> None:
        async with _session() as db:
            user = await jit_provision_user(db, {"sub": SUB, "email": EMAIL})
            await _assert_only_user(db, user.id, SUB, "")

    asyncio.run(run())


def test_callback_refusals_return_identical_responses(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(oidc_routes, "is_oidc_enabled", lambda: True)
    monkeypatch.setattr(
        oidc_routes, "get_oauth_client", lambda: SimpleNamespace(oidc=object())
    )
    authorize = AsyncMock()
    monkeypatch.setattr(oidc_routes, "authorize_access_token", authorize)
    cache_put = Mock()
    monkeypatch.setattr(oidc_routes, "cache_put", cache_put)
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.DEBUG, logger="server")

    async def call_callback(
        existing_sub: str | None, verified: bool, reason: str
    ) -> bytes:
        async with _session() as db:
            user_id = await _seed_user(db, existing_sub)
            authorize.return_value = {
                "userinfo": {"sub": SUB, "email": EMAIL, "email_verified": verified}
            }

            async def override_db() -> AsyncIterator[AsyncSession]:
                yield db

            app = FastAPI()
            app.include_router(oidc_routes.router)
            app.dependency_overrides[get_db] = override_db
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.get(
                    "/api/auth/oidc/callback?code=test&state=test"
                )

            assert response.status_code == 403
            assert response.json() == {"detail": OIDC_BIND_REFUSED_DETAIL}
            assert "set-cookie" not in response.headers
            assert any(
                record.name == oidc_routes.__name__
                and record.levelno == logging.WARNING
                and reason in record.getMessage()
                and SUB in record.getMessage()
                for record in caplog.records
            )
            await _assert_only_user(db, user_id, existing_sub, LOCAL_HASH)
            assert (await db.scalars(select(OidcRefreshSession))).all() == []
            body: bytes = response.content
            return body

    async def run() -> None:
        unverified = await call_callback(None, False, "email not verified")
        linked = await call_callback(OTHER_SUB, True, "account already linked")
        assert unverified == linked

    asyncio.run(run())
    assert all(EMAIL not in record.getMessage() for record in caplog.records)
    cache_put.assert_not_called()
