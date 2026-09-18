---
status: draft
issue: 1314
intent: intent/2026-09-18-1314-model-migration-drift.md
---

# Spec: Models and migrations describe the same schema — and CI keeps it so

Decisions carried from intent review: align the **models to the migrations**
throughout (no live-DB change), flag anything that looks like a real schema bug
separately; put the gate in the **existing P1 Postgres suite**.

## Findings — every difference `alembic check` reports on dev

| Item | Migration (= production) | Model today | Fix (model side) |
| --- | --- | --- | --- |
| `git_credentials.kind` | `server_default="pat"` | `default="pat"` (Python only) | add `server_default="pat"` (keep `default`) |
| `test_target_credentials.kind` | `server_default="form"` | `default="form"` (Python only) | add `server_default="form"` (keep `default`) |
| `git_credentials.org_id` | `op.create_index("ix_git_credentials_org_id")` | no index | `index=True` (SQLAlchemy names it `ix_git_credentials_org_id`) |
| `test_target_credentials.org_id` | `ix_test_target_credentials_org_id` | no index | `index=True` |
| `kms_data_keys.org_id` | column `unique=True` (PG unique **constraint**) **plus** a separate non-unique `ix_kms_data_keys_org_id` | `unique=True, index=True` (renders as one unique **index**) | keep `unique=True`, drop `index=True`, add `Index("ix_kms_data_keys_org_id", "org_id")` in `__table_args__` |
| `kms_data_keys.kms_key_id` comment | short text | longer text (adds the fernet/ARN detail) | use the migration's text; keep the extra detail as a Python comment beside the column |
| `kms_data_keys.rotated_at` comment | short text | reworded text | use the migration's text |

**No real schema bug found:** `kms_data_keys.org_id` is unique in both — one
data key per org — they only express it differently. Nothing to escalate.

## Design

1. `server/database/models.py` — the seven edits above, nothing else. No
   Alembic revision: the database already matches the migrations.
2. **Gate:** new test in `tests/postgres/test_p1_alembic.py`,
   `test_models_match_migrations`: on the P1 test database, `alembic upgrade
   head` then `alembic check` (both via the existing `run_alembic` helper);
   assert `check` exits 0, failing with the reported operations. It runs in the
   existing `postgres (P1 acceptance)` CI job (PG 15 + 16) — no workflow edit.

## Alternatives rejected

- **Migrations to models** (an Alembic revision changing defaults/indexes/
  comments) — a live-DB change the intent excludes; production is the
  reference.
- **Keep the richer comments by migrating the DB comments** — a DB change for
  documentation; the detail lives equally well as a code comment.
- **A separate CI job for the gate** — the P1 job already provisions Postgres.

## Risks

- **The gate's database state:** `alembic check` must run against a database
  at head. `test_alembic_upgrade_head_on_empty_postgres` already upgrades the
  P1 DB; the new test runs `upgrade head` itself (idempotent — proven by
  `test_alembic_upgrade_idempotent`) so it does not depend on order.
- **SQLite suites** create tables from the models; adding `server_default` and
  indexes changes nothing observable there.
- **`index=True` names:** must match the migrations' names exactly, or
  autogenerate reports remove+add — covered by the gate itself.

## Verification

- Locally on a throwaway `postgres:15`: `alembic upgrade head && alembic check`
  → non-zero on dev with exactly the seven differences (recorded first);
  exit 0 with the change.
- `test_models_match_migrations` fails on dev (written first), passes after.
- **Mutation:** revert one model edit (e.g. drop `index=True`) → the gate test
  fails naming that operation.
- Backend + web-server suites, the full P1 suite, ratchet green.
