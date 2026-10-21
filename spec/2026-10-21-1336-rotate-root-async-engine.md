---
status: approved
issue: 1336
intent: intent/2026-10-21-1336-rotate-root-async-engine.md
---

# Spec: drive `rotate-root` through the async engine

## Design

In `_cmd_rotate_root` (`apps/web-server/server/crypto/__main__.py`):

- Replace `create_engine(sync_url)` with
  `create_async_engine(db_url)` — the `DATABASE_URL` as the service uses it
  (asyncpg in production, aiosqlite locally).
- Run the rotation as
  `asyncio.run(...)` of a coroutine doing
  `async with engine.connect() as conn: return await conn.run_sync(lambda c: rotate_root(c, ...))`,
  then `await engine.dispose()`.
- `engine.connect()`, not `engine.begin()`: the connection starts outside a
  transaction, so each `Session(conn)` in `rotate_root` begins and commits its
  own batch transaction, as it did with the sync engine. `begin()` would turn
  the whole rotation into one outer transaction.
- Delete the misleading psycopg2 comment and the URL rewrite.

`rotate_root` (`rotation.py`) already does `Session(sync_engine)`; `Session`
accepts an `Engine` or a `Connection`. Only the parameter's type hint and
docstring change (`Engine | Connection`), name kept for direct callers.

## Alternatives rejected

- Add `psycopg[binary]` / `psycopg2-binary` to the image: a driver shipped
  only for an ops CLI; #1336 prefers no new dependency.
- Require an operator-supplied sync URL: moves the defect to the runbook.

## Risks

- A bare `postgresql://` `DATABASE_URL` would now fail in `create_async_engine`
  (it needs an async driver). The service itself already requires an async URL
  for the same variable, so the CLI matches the service.

## Verification

New test in `tests/secrets/test_p2_rotation.py` runs `main(["rotate-root", ...])`
against a `sqlite+aiosqlite` file DB with `psycopg2`/`psycopg` made
unimportable and the module-level sync `create_engine` replaced by one that
raises. It asserts exit 0 and that every row is re-wrapped under the new key.
It fails on the current code (the sync engine is built). Existing rotation
tests stay green; ruff clean.
