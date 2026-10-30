"""Pool caps for the main database and per-dispatch verify engines (#1469)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = _WEB_SERVER.parent / "backend"
for _path in (_WEB_SERVER, _BACKEND):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agents import verify_dispatch  # noqa: E402
from server.db_pool import pool_kwargs  # noqa: E402

POSTGRES_URL = "postgresql+asyncpg://u:p@127.0.0.1:1/db"


def test_pool_kwargs_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)

    assert pool_kwargs(POSTGRES_URL, "DB", 2, 2) == {
        "pool_size": 2,
        "max_overflow": 2,
        "pool_timeout": 10,
    }


def test_pool_kwargs_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "4")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "15")

    assert pool_kwargs(POSTGRES_URL, "DB", 2, 2) == {
        "pool_size": 4,
        "max_overflow": 3,
        "pool_timeout": 15,
    }


def test_pool_kwargs_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "invalid")
    assert pool_kwargs("sqlite+aiosqlite:///test.db", "DB", 2, 2) == {}


def test_pool_kwargs_bad_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "invalid")
    with pytest.raises(ValueError):
        pool_kwargs(POSTGRES_URL, "DB", 2, 2)


@pytest.mark.asyncio
async def test_main_engine_pool_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)

    engine = create_async_engine(POSTGRES_URL, **pool_kwargs(POSTGRES_URL, "DB", 2, 2))
    try:
        assert isinstance(engine.pool, AsyncAdaptedQueuePool)
        assert engine.pool.size() == 2
        assert engine.pool._max_overflow == 2
        assert engine.pool._timeout == 10
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verify_engine_pool_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VERIFY_DB_POOL_SIZE", "VERIFY_DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        verify_dispatch,
        "_import_engine_module",
        lambda: SimpleNamespace(
            _resolve_database_url=lambda: POSTGRES_URL,
            _connect_args_for=lambda _url: {},
        ),
    )

    engine, _factory = verify_dispatch._fresh_store_engine()
    try:
        assert isinstance(engine.pool, AsyncAdaptedQueuePool)
        assert engine.pool.size() == 1
        assert engine.pool._max_overflow == 0
        assert engine.pool._timeout == 10
    finally:
        await engine.dispose()
