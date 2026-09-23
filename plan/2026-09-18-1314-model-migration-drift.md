---
status: approved
issue: 1314
spec: spec/2026-09-18-1314-model-migration-drift.md
---

# Plan: Models and migrations describe the same schema — and CI keeps it so

Approved decisions (self-contained):

- Align the **models to the migrations** (production is the reference); no
  Alembic revision, no live-DB change.
- Seven model edits in `apps/web-server/server/database/models.py`:
  1. `GitCredential.kind`: add `server_default="pat"` (keep `default`);
  2. `TestTargetCredential.kind`: add `server_default="form"` (keep `default`);
  3. `GitCredential.org_id`: `index=True`;
  4. `TestTargetCredential.org_id`: `index=True`;
  5. `KmsDataKey.org_id`: keep `unique=True`, drop `index=True`, add
     `Index("ix_kms_data_keys_org_id", "org_id")` to `__table_args__`
     (non-unique, as the migration creates it);
  6. `KmsDataKey.kms_key_id` comment → the migration's text; the fernet/ARN
     detail moves to a Python comment beside the column;
  7. `KmsDataKey.rotated_at` comment → the migration's text.
- Gate: `test_models_match_migrations` in `tests/postgres/test_p1_alembic.py`:
  `alembic upgrade head` then `alembic check` via the existing `run_alembic`
  helper; assert exit 0 (message shows the reported operations). Runs in the
  existing `postgres (P1 acceptance)` job — no workflow change.
- No real schema bug: `kms_data_keys.org_id` is unique on both sides.

Branch: `fix/1314-model-migration-drift` (off `origin/dev`).

## Steps

1. **Record the drift first** on a throwaway `postgres:15` (podman, port
   55432): `alembic upgrade head` then `alembic check` on unmodified dev.
   → verify non-zero with exactly the seven differences in the spec's table.
   If the list differs (e.g. dev moved), STOP, update the spec and re-ask.
2. **Gate test first:** add `test_models_match_migrations`.
   → verify it FAILS on dev against the throwaway PG (fresh database).
3. `models.py`: the seven edits.
   → verify `alembic check` exits 0 on a fresh DB upgraded to head, and the
   gate test passes.
4. **Mutation check:** revert edit 3 (`index=True` on
   `GitCredential.org_id`) → the gate test fails naming
   `ix_git_credentials_org_id`; restore.
5. Suites: backend + web-server (SQLite create-from-models unaffected), the
   full P1 suite on a fresh throwaway PG (the `test_p1_suite_against_postgres`
   meta-test is judged by CI, as in #1308 — it fails on this workstation for
   unmodified dev too); ratchet (pinned, on the commit) + format.
6. Commit, push, PR to `dev` linking intent/spec/plan; merge when CI (incl.
   `postgres (P1 acceptance)` on PG 15 + 16) is green; close #1314.

## Tests

```bash
podman run --rm -d --name tf-pg-1314 -p 55432:5432 -e POSTGRES_USER=tfactory_test \
  -e POSTGRES_PASSWORD=tfactory_test -e POSTGRES_DB=tfactory_test postgres:15
cd apps/web-server && DATABASE_URL=postgresql+asyncpg://tfactory_test:tfactory_test@localhost:55432/tfactory_test \
  KMS_BACKEND=fernet KMS_FERNET_KEY=<random> ../backend/.venv/bin/alembic upgrade head && ../backend/.venv/bin/alembic check
TEST_POSTGRES_URL=postgresql+asyncpg://tfactory_test:tfactory_test@localhost:55432/tfactory_test \
  TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/postgres/test_p1_alembic.py -m postgres -q
podman stop tf-pg-1314
```

(Reset the throwaway DB's `public` schema between the upgrade/check runs and
the pytest run so each starts empty.)

## Rollback

Revert the commit. Model-side only: the live database already matches the
migrations, so reverting only reintroduces the drift and removes the gate.
