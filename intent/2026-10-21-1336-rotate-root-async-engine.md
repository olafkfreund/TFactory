---
status: draft
issue: 1336
author: olafkfreund
---

# Intent: `crypto rotate-root` runs in the deployed image against Postgres

Covers #1336 and its duplicate #1355.

## Problem

`python -m server.crypto rotate-root` builds a sync SQLAlchemy engine
(`apps/web-server/server/crypto/__main__.py:113-114`), rewriting
`+asyncpg` to `+psycopg2`. The runtime image installs only
`apps/web-server/requirements.txt` and `apps/backend/requirements.txt`
(`Dockerfile:343-345`); neither ships a sync Postgres driver
(`psycopg2-binary` is only in `tests/requirements-test.txt:30`). Against
Postgres the CLI raises `ModuleNotFoundError` before doing any work, so
KMS root rotation cannot run in production. The comment at `:109-112`
says "only psycopg2 is installed", which is wrong for the image.

## Proposed outcome

An operator can run `rotate-root` in the deployed image against the
configured `DATABASE_URL` (asyncpg or aiosqlite) with no new dependency.

## Affected users and systems

Operators rotating the KMS root; the web-server image. No request path.

## Constraints

- No new runtime dependency (option 1 of #1336).
- `rotate_root()` keeps its per-batch commit behaviour and its public shape
  for scripts that call it directly with a sync engine.

## Open questions

None.
