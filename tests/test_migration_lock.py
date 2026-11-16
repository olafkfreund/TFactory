"""Unit and SQLite tests for Postgres migration advisory lock (#1488)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEB_SERVER_DIR = _REPO_ROOT / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.database import engine as engine_mod  # noqa: E402


class _FakeConnEngine:
    """Fake async engine + connection recording advisory lock lifecycle."""

    def __init__(
        self,
        calls: list[str],
        exec_opts: list[dict[str, object]],
        *,
        fail_unlock: bool = False,
    ) -> None:
        self.calls = calls
        self.exec_opts = exec_opts
        self.fail_unlock = fail_unlock

    def connect(self) -> _FakeConnEngine:
        return self

    async def __aenter__(self) -> _FakeConnEngine:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def execution_options(self, **kw: object) -> _FakeConnEngine:
        self.exec_opts.append(kw)
        return self

    async def execute(self, stmt: object, params: object = None) -> None:
        _ = params
        sql = str(stmt)
        if self.fail_unlock and "pg_advisory_unlock" in sql:
            raise RuntimeError("unlock failed")
        self.calls.append(sql)

    async def dispose(self) -> None:
        self.calls.append("dispose")


def test_sqlite_init_db_does_not_use_advisory_lock(tmp_path: Path) -> None:
    """SQLite boot runs migrations directly without pg_advisory_lock."""
    db_path = tmp_path / "migration.db"
    code = """
import asyncio, sys
sys.path.insert(0, 'apps/web-server')
from sqlalchemy import event
from server.database.engine import engine, init_db

@event.listens_for(engine.sync_engine, 'before_cursor_execute')
def _log_sql(conn, cursor, statement, parameters, context, executemany):
    print(statement)

asyncio.run(init_db())
"""
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "APP_MIGRATIONS_AUTO_APPLY": "true",
            "APP_DISABLE_AUTH": "true",
            "GRAPHITI_ENABLED": "false",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"init_db exit {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )
    assert "PRAGMA journal_mode" in result.stdout
    assert "pg_advisory" not in result.stdout.lower()


def test_postgres_upgrade_runs_inside_lock_with_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_upgrade_head_with_pg_lock sets lock_timeout, locks, upgrades, unlocks, disposes."""
    calls: list[str] = []
    exec_opts: list[dict[str, object]] = []
    engine_kwargs: list[dict[str, object]] = []
    fake = _FakeConnEngine(calls, exec_opts)

    def _fake_create_async_engine(_url: str, **kw: object) -> _FakeConnEngine:
        engine_kwargs.append(kw)
        return fake

    monkeypatch.setattr(engine_mod, "create_async_engine", _fake_create_async_engine)
    monkeypatch.setattr(
        engine_mod,
        "_alembic_upgrade_head_sync",
        lambda: calls.append("upgrade"),
    )

    asyncio.run(engine_mod._upgrade_head_with_pg_lock())

    assert engine_kwargs == [{"poolclass": NullPool}]
    assert exec_opts == [{"isolation_level": "AUTOCOMMIT"}]
    assert calls == [
        "SET lock_timeout = '300s'",
        "SELECT pg_advisory_lock(:k)",
        "upgrade",
        "SELECT pg_advisory_unlock(:k)",
        "dispose",
    ]


def test_unlock_failure_does_not_mask_upgrade_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If both upgrade and pg_advisory_unlock raise, the upgrade error propagates."""
    calls: list[str] = []
    exec_opts: list[dict[str, object]] = []
    fake = _FakeConnEngine(calls, exec_opts, fail_unlock=True)

    monkeypatch.setattr(
        engine_mod,
        "create_async_engine",
        lambda _url, **_kw: fake,
    )

    def _boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(engine_mod, "_alembic_upgrade_head_sync", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(engine_mod._upgrade_head_with_pg_lock())

    assert "dispose" in calls
