---
status: draft
issue: 1488
author: olafkfreund
---

# Intent: serialise `alembic upgrade head` at boot across pods

## Problem

`init_db()` (`apps/web-server/server/database/engine.py:194`) runs `alembic
upgrade head` at every boot when `MIGRATIONS_AUTO_APPLY` is true, the default.
Nothing serialises it across pods. Two pods that start together (KEDA
scale-up, node restart, crash loop) both run the same upgrade: the loser fails
with a `UniqueViolationError` on `alembic_version`, or runs data steps twice.
PFactory had the same gap (#981) and fixed it in PR #982.

## Proposed outcome

Two pods booting at the same time against one Postgres database both start,
and each pending migration is applied once. SQLite (local dev) behaves as today.

## Affected users and systems

TFactory web-server pods (Postgres); local dev (SQLite, unchanged).

## Constraints

- Port PFactory #982 faithfully; no Argo hook Job, no gitops change, no new dependency.
- `MIGRATIONS_AUTO_APPLY=false` path unchanged.
- Lock key distinct from PFactory's and AIFactory's.

## Open questions

None. The user chose the PFactory advisory-lock port.
