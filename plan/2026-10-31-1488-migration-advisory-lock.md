---
status: approved
issue: 1488
spec: spec/2026-10-31-1488-migration-advisory-lock.md
coders:
  1: agy   # lead assigned agy to this lane
  2: agy
---

# Plan: Postgres advisory lock around boot migrations

Approved decisions (from the spec; port of PFactory PR #982 with its review fixes):
- `_upgrade_head_with_pg_lock()` on a dedicated `NullPool` async engine, one
  AUTOCOMMIT connection: `SET lock_timeout = '300s'`, `SELECT pg_advisory_lock(:k)`,
  upgrade via `asyncio.to_thread(_alembic_upgrade_head_sync)`, unlock in `finally`,
  engine disposed in an outer `finally`.
- Unlock failure in `finally` is logged (`logger.exception`), never raised over the original error.
- `_MIGRATION_LOCK_KEY = 0x54464143544F5259` ("TFACTORY"), distinct from PFactory's `0x50464143544F5259`.
- Postgres only; SQLite no lock; `MIGRATIONS_AUTO_APPLY=false` unchanged.
- `import asyncio` at module level; drop the local one in `init_db()`.

## Steps

1. `apps/web-server/server/database/engine.py`: add `import asyncio` and
   `from sqlalchemy.pool import NullPool` to the top imports; add
   `_MIGRATION_LOCK_KEY` with a one-line why comment after `_ALEMBIC_INI_PATH`
   (line 106); add `_upgrade_head_with_pg_lock()` after `_alembic_upgrade_head_sync`
   (~line 135); in `init_db()` (lines 188-194) remove `import asyncio` and branch
   on `engine.dialect.name == "postgresql"`.
   → verify by `apps/backend/.venv/bin/ruff check apps/web-server/server/database/engine.py`
   Traps: `engine`/`DATABASE_URL` are bound at import; keep `text()` import.
2. Tests:
   a. `tests/postgres/test_p1_alembic.py`: add an optional `barrier_dir` to
      `_init_db_subprocess` (env `INIT_DB_BARRIER`, file barrier after imports,
      30 s deadline) and `test_concurrent_init_db_applies_migrations_once`
      (`postgres`, `slow`): `_drop_schema`, two subprocesses via a
      `ThreadPoolExecutor`, both exit 0, `alembic_version` == [script head]
      (head from a local alembic `Config` on `WEB_SERVER_ROOT`).
   b. New `tests/test_migration_lock.py`: SQLite subprocess `init_db()` issues no
      `pg_advisory` SQL; fake-engine unit test asserts the order
      SET lock_timeout, lock, upgrade, unlock, dispose and that
      `execution_options` got `isolation_level="AUTOCOMMIT"`; a third test: upgrade
      raises, unlock raises, the upgrade's error propagates and dispose still runs.
   → verify by `apps/backend/.venv/bin/pytest tests/test_migration_lock.py -q`
   and `TEST_POSTGRES_URL=... apps/backend/.venv/bin/pytest tests/postgres/test_p1_alembic.py -m postgres -q`.
   Traps: Postgres tests skip without `TEST_POSTGRES_URL`; never `git add -A` (venv symlink).

## Tests

- ruff check + format check on touched files: clean.
- `pytest tests/test_migration_lock.py -q`: pass.
- Postgres suite: pass; the concurrency test fails with engine.py from `origin/dev`.

## Rollback

Revert the PR; boot migrations go back to unlocked. No schema or data change.

## Deviations

- Step 1: agy also deleted the unused `from .models import Base`; the lead
  restored it (out of scope; same two pre-existing ruff findings as `origin/dev`).
  The lead added PFactory's one-line comment on `SET lock_timeout`.
- Step 2a: agy moved the existing local `asyncio`/`sqlalchemy`/`urlparse`
  imports in `test_p1_alembic.py` to module level (no redundant local imports).
  The alembic `Config`/`ScriptDirectory` imports stay local to the new test so
  the module still collects where alembic is absent (`alembic_available()` skips).
- Step 2a result: Postgres 18 locally, the concurrency test fails 5/5 with
  `engine.py` from `origin/dev` (`UniqueViolationError` on `alembic_version`)
  and passes 5/5 with the fix; the whole file passes (8 tests).
