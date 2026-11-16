---
status: approved
issue: 1488
intent: intent/2026-10-31-1488-migration-advisory-lock.md
---

# Spec: Postgres advisory lock around boot migrations

## Design

Port PFactory PR #982 (including its lead-review deviations) to
`apps/web-server/server/database/engine.py`:

- New `_upgrade_head_with_pg_lock()`: create a dedicated
  `create_async_engine(DATABASE_URL, poolclass=NullPool)`, open one connection,
  set `isolation_level="AUTOCOMMIT"`, run `SET lock_timeout = '300s'`, then
  `SELECT pg_advisory_lock(:k)` on `_MIGRATION_LOCK_KEY`, then the existing
  `_alembic_upgrade_head_sync` via `asyncio.to_thread`, then
  `SELECT pg_advisory_unlock(:k)` in `finally`. Dispose the engine in an outer
  `finally`. The NullPool engine keeps the lock connection out of the app pool,
  which may be used from another event loop (the failure PFactory hit in CI).
- If the unlock fails in `finally`, log it (`logger.exception`) and do not
  raise, so it never masks the upgrade's original error. The session lock is
  released anyway when the connection closes.
- `init_db()`: when `MIGRATIONS_AUTO_APPLY` is true and
  `engine.dialect.name == "postgresql"`, call the helper; otherwise today's
  bare `asyncio.to_thread(...)`. SQLite takes no lock. `MIGRATIONS_AUTO_APPLY=false` unchanged.
- `asyncio` moves to a module import; the local `import asyncio` in
  `init_db()` goes (no redundant local imports).
- Key: `_MIGRATION_LOCK_KEY = 0x54464143544F5259` (ASCII "TFACTORY").
  PFactory uses `0x50464143544F5259` ("PFACTORY"); AIFactory's lane picks its own.

## Alternatives rejected

- Pre-sync Argo hook Job: gitops change and DDL rights in a Job; the user chose the lock.
- `pg_advisory_xact_lock` in `env.py`: affects every CLI run; bigger change.
- `pg_try_advisory_lock` and skip: a skipping pod could serve before the schema is at head.

## Risks

- A hung migration blocks other booting pods; after 300 s they fail boot and
  the kubelet restarts them (loud, as intended).
- Cancel during boot: if `init_db()` is cancelled while the upgrade thread
  runs, `finally` releases the lock while the thread keeps migrating. Accepted
  in the PFactory #982 review.
- One extra Postgres connection for the duration of the boot upgrade.

## Verification

- `tests/postgres/test_p1_alembic.py::test_concurrent_init_db_applies_migrations_once`
  (`postgres`, `slow`; CI `postgres-acceptance`): two `init_db()` subprocesses
  held at a file barrier, both exit 0, `alembic_version` has one row at head.
  Must fail against `origin/dev`'s engine.py.
- `tests/test_migration_lock.py`: SQLite `init_db()` issues no `pg_advisory`
  SQL; Postgres helper order is SET lock_timeout, lock, upgrade, unlock,
  dispose, with `isolation_level="AUTOCOMMIT"` asserted; an unlock failure
  after a failed upgrade re-raises the upgrade error.
