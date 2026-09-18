"""A tz-aware datetime written to a naive column must commit on Postgres (#1308).

Every model ``DateTime`` column is ``timestamp without time zone``, and five
writers stored ``datetime.now(UTC)``-style aware values in them. SQLite accepts
that, so every suite passed; asyncpg rejects it ("can't subtract offset-naive
and offset-aware datetimes"), so on the deployed Postgres: minting an API key
with an expiry returned 500, ``ApiKey.last_used_at`` was never recorded,
resolving a test-target credential raised, and the email token expiry could not
be stored. These run the writers' exact values against a real Postgres.
"""

from __future__ import annotations

import base64
import importlib
import secrets
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_WEB_SERVER = Path(__file__).resolve().parents[2] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

_SCHEMA = "tz_columns_rt_test"
_TABLES = (
    "users",
    "organizations",
    "api_keys",
    "test_target_credentials",
    "email_accounts",
)
# Same set tests/secrets/helpers.py re-imports, so the encrypted columns see
# the KMS env set below.
_CRYPTO_MODULES = (
    "server.database.engine",
    "server.database",
    "server.crypto.encrypted_string",
    "server.crypto.kms",
    "server.crypto",
)


@pytest.fixture
def fernet_kms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypted columns need a KMS; restored by monkeypatch on teardown."""
    monkeypatch.setenv("KMS_BACKEND", "fernet")
    monkeypatch.setenv(
        "KMS_FERNET_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    )
    for name in _CRYPTO_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.import_module("server.crypto")


@pytest_asyncio.fixture
async def pg(test_postgres_url: str, fernet_kms: None):
    """Session on an isolated schema with one user and one org to own rows."""
    from server.database.models import Base, Organization, User

    # search_path on EVERY pooled connection, not just the first one.
    engine = create_async_engine(
        test_postgres_url,
        future=True,
        connect_args={"server_settings": {"search_path": _SCHEMA}},
    )
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{_SCHEMA}"'))
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Base.metadata.tables[n] for n in _TABLES]
            )
        )
    session = async_sessionmaker(engine, expire_on_commit=False)()
    session.add(User(id="u1", password_hash="x"))
    session.add(Organization(id="o1", name="o", slug="o", owner_id="u1"))
    await session.commit()
    try:
        yield session
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        await engine.dispose()


async def test_an_api_key_with_an_expiry_commits_as_the_right_instant(pg) -> None:
    """The 500: routes/api_keys.py computes exactly this value."""
    from server.database.models import ApiKey

    expires = datetime.now(UTC) + timedelta(days=7)
    # A non-UTC offset must land on the same instant, not the same wall time.
    plus_two = datetime(2026, 9, 25, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    pg.add(
        ApiKey(
            id="k1",
            user_id="u1",
            org_id="o1",
            key_hash="p$1",
            name="a",
            expires_at=expires,
        )
    )
    pg.add(
        ApiKey(
            id="k2",
            user_id="u1",
            org_id="o1",
            key_hash="p$2",
            name="b",
            expires_at=plus_two,
        )
    )
    await pg.commit()

    k1, k2 = await pg.get(ApiKey, "k1"), await pg.get(ApiKey, "k2")
    await pg.refresh(k1)
    await pg.refresh(k2)
    assert k1.expires_at == expires.replace(tzinfo=None)
    assert k2.expires_at == datetime(2026, 9, 25, 10, 0)


async def test_stamping_an_api_keys_last_use_commits(pg) -> None:
    """mcp_remote/auth.py stamps this on every key use; the failure was swallowed."""
    from server.database.models import ApiKey

    pg.add(ApiKey(id="k3", user_id="u1", org_id="o1", key_hash="p$3", name="c"))
    await pg.commit()
    row = await pg.get(ApiKey, "k3")
    row.last_used_at = datetime.now(UTC)
    await pg.commit()

    await pg.refresh(row)
    assert row.last_used_at is not None


async def test_resolving_a_test_target_credential_works(pg) -> None:
    """credential_resolver's commit raised, so the lane login never resolved."""
    from server.database.models import TestTargetCredential
    from server.services.credential_resolver import resolve_store_credential

    pg.add(
        TestTargetCredential(
            id="tc_1",
            org_id="o1",
            name="login",
            kind="form",
            username="qa@acme.test",
            secret="s3cr3t",
        )
    )
    await pg.commit()

    assert await resolve_store_credential(pg, "tc_1") == ("qa@acme.test", "s3cr3t")


async def test_an_email_token_expiry_commits(pg) -> None:
    """routes/email.py and services/email_service.py compute exactly this."""
    from server.database.models import EmailAccount

    pg.add(
        EmailAccount(
            user_id="u1",
            provider="gmail",
            email_address="a@acme.test",
            access_token="tok",
            token_expiry=datetime.now(UTC) + timedelta(seconds=3600),
        )
    )
    await pg.commit()
