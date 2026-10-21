---
status: approved
issue: 1336
spec: spec/2026-10-21-1336-rotate-root-async-engine.md
---

# Plan: drive `rotate-root` through the async engine

Closes #1336 and #1355. `rotate-root` must open `DATABASE_URL` with
`create_async_engine` (asyncpg/aiosqlite, already in the image) instead of a
sync `+psycopg2` engine that the image cannot import. No new dependency.
Rotation runs via `async with engine.connect() as conn: await
conn.run_sync(lambda c: rotate_root(c, ...))` under `asyncio.run`, then
`engine.dispose()`. Use `connect()`, not `begin()`, so each batch's
`Session(conn)` commits its own transaction as before.

## Steps

1. `apps/web-server/server/crypto/__main__.py:40-45,108-135`: drop the
   `create_engine` import and the sync URL rewrite + its comment; import
   `asyncio` and `sqlalchemy.ext.asyncio.create_async_engine`; build the async
   engine from `db_url` and run `rotate_root` through `conn.run_sync` as above,
   disposing the engine in a `finally`. Backend construction stays before it.
   → verify by `ruff check` and step 3's test.
   Traps: `asyncio.run` must not be called from a running loop (CLI only, fine).
2. `apps/web-server/server/crypto/rotation.py`: type `sync_engine` as
   `Engine | Connection` and update the docstring (the CLI passes the sync
   `Connection` facade from `run_sync`). No logic change.
   → verify by existing `tests/secrets/test_p2_rotation.py`.
   Traps: keep the parameter name; external scripts may pass it positionally.
3. `tests/secrets/test_p2_rotation.py`: add
   `test_cli_rotate_root_uses_async_engine` — seed a `sqlite+aiosqlite` temp
   file DB (sync engine on the same file for setup), set `DATABASE_URL`,
   `KMS_BACKEND=fernet`, `KMS_FERNET_KEY`, `KMS_FERNET_KEY_NEW` via
   `monkeypatch`, block `psycopg2`/`psycopg` in `sys.modules`, replace
   `server.crypto.__main__.create_engine` with a raiser (`raising=False`),
   call `main([...])`; assert exit 0 and every row decrypts under the NEW key.
   Mark `secrets` like its neighbours. → must fail on the old code.
   Traps: `reimport_crypto` first so the lazy model imports work.

## Tests

`apps/backend/.venv/bin/pytest tests/secrets/test_p2_rotation.py -m secrets -v`
→ all pass; the new test fails with step 1 reverted.
`ruff check apps/web-server/server/crypto tests/secrets` → clean.

## Rollback

Revert the PR; the CLI returns to the sync engine (still broken on Postgres).
