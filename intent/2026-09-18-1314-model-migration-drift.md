---
status: approved
issue: 1314
author: Olaf Krasicki-Freund
---

# Intent: Models and migrations describe the same schema — and CI keeps it so

## Problem

`alembic check` against a database upgraded to head fails on `dev`
(found while verifying #1308; the drift is unrelated to that change). The ORM
models and the Alembic migrations disagree on:

- server defaults on `git_credentials.kind` and `test_target_credentials.kind`
  (migrations set one, models don't declare it);
- `org_id` indexes on `git_credentials` and `test_target_credentials`
  (in the migrations, absent from the models);
- `kms_data_keys`: two column comments that differ, and the `org_id`
  uniqueness expressed as a unique constraint in one and a unique index in the
  other.

Runtime is unaffected today (production runs the migrations). The cost is
that `alembic revision --autogenerate` cannot be trusted: the next generated
migration would try to drop those indexes, defaults and the constraint along
with whatever it was meant to capture — an easy way to lose an index in
production. And nothing stops the drift from growing.

## Proposed outcome

- `alembic check` against a database upgraded to head reports **no
  differences** on `dev`.
- CI fails a PR that introduces new model/migration drift.
- Production's schema is unchanged by the fix (the migrations are the source
  of truth for what exists).

## Affected users and systems

- `apps/web-server/server/database/models.py`; possibly a no-op Alembic
  revision if a difference can only be settled on the migration side
- CI: the `postgres (P1 acceptance)` job (already has a live PG)
- Anyone writing the next migration

## Constraints

- **No schema change on the live database** unless the approver chooses one:
  align the models to what the migrations created, not the reverse.
- Must keep #1308's `UTCDateTime` columns and #1257's migration `__all__`
  convention.
- The CI gate must run against a real Postgres (SQLite reflects defaults,
  indexes and comments differently).

## Open questions

1. For each difference, models-to-migrations (default, no DB change) —
   except where the migration looks wrong (e.g. whether `kms_data_keys.org_id`
   is meant to be unique at all)? Recommend models-to-migrations throughout,
   and flag any case that looks like a real schema bug for a separate decision.
2. Gate placement: extend the existing `postgres (P1 acceptance)` job with
   `alembic upgrade head && alembic check`, or a separate job? Recommend
   extending the existing job (it already provisions Postgres).
