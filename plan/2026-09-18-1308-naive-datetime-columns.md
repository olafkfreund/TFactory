---
status: approved
issue: 1308
spec: spec/2026-09-18-1308-naive-datetime-columns.md
---

# Plan: Timestamps must survive Postgres — no tz-aware values into naive columns

Approved decisions (self-contained):

- Stored convention: **naive UTC**. No column-type change, **no Alembic
  migration**, no change to stored data or to reads.
- **`UTCDateTime`** in new `apps/web-server/server/database/types.py`: a
  `TypeDecorator` with `impl = DateTime` (naive), `cache_ok = True`.
  `process_bind_param`: `None` → `None`; aware → `.astimezone(UTC)` then
  `.replace(tzinfo=None)`; naive → unchanged. `process_result_value`: unchanged.
- **Every** `DateTime` column in `server/database/models.py` (~33) uses
  `UTCDateTime`. This fixes the five broken writers (`api_keys.expires_at`,
  `ApiKey.last_used_at`, `TestTargetCredential.last_used_at` via
  `credential_resolver`, `EmailAccount.token_expiry` on connect and refresh)
  **without editing any call site**; `job_state_store._now_naive()` stays.
- A **guard test** walks `Base.metadata` and fails on any plain `DateTime`
  column.
- Proof on real Postgres in `tests/postgres/` (marker `postgres`, CI's P1
  job), written failing first against a local throwaway PG container.

Branch: `fix/1308-naive-datetime-columns` (off `origin/dev`).

## Steps

1. **Postgres acceptance test first (must FAIL on dev).** New
   `tests/postgres/test_p1_datetime_columns_postgres.py`, same pattern as
   `test_p1_job_state_postgres.py` (`pg_session` via `create_all`,
   `pytestmark = [postgres, slow]`):
   - `ApiKey` inserted with `expires_at = datetime.now(UTC) + timedelta(days=7)`
     (the route's exact value) commits, and reads back as the same instant;
   - `ApiKey.last_used_at = datetime.now(timezone.utc)` commits;
   - the real `credential_resolver` function for a `TestTargetCredential`
     returns its credentials (its commit currently raises);
   - `EmailAccount.token_expiry` set aware commits.
   Run against `podman run --rm -d -p 55432:5432 -e POSTGRES_USER=tfactory_test
   -e POSTGRES_PASSWORD=tfactory_test -e POSTGRES_DB=tfactory_test postgres:15`
   with `TEST_POSTGRES_URL=postgresql+asyncpg://tfactory_test:tfactory_test@localhost:55432/tfactory_test`.
   → verify all four FAIL on dev with asyncpg `DataError` ("can't subtract
   offset-naive and offset-aware datetimes"). If they do not fail for that
   reason, STOP, update the spec/plan and re-ask.
2. **Unit tests** in `apps/web-server/tests/test_utc_datetime.py`:
   - bind: aware UTC → naive same wall time; aware `+02:00` → naive UTC
     (2 h earlier); naive → unchanged; `None` → `None`;
   - guard: every column in `Base.metadata.sorted_tables` whose type is a
     `DateTime` is a `UTCDateTime`;
   - SQLite round-trip: `ApiKey` with aware `expires_at` reads back naive UTC.
   → verify they fail on dev (import error / guard lists the plain columns).
3. `server/database/types.py`: implement `UTCDateTime`.
   → verify the bind unit tests pass.
4. `server/database/models.py`: replace every `DateTime` column type with
   `UTCDateTime` (keep the `DateTime` import only if still referenced).
   → verify the guard test, the SQLite round-trip, and the four PG tests pass;
   existing web-server + backend suites green.
5. **Alembic unchanged:** run autogenerate/`alembic check` against a scratch DB
   upgraded to head.
   → verify no diff is detected (the column type is still
   `TIMESTAMP WITHOUT TIME ZONE`).
6. **Mutation checks:** revert one model column to `DateTime` → guard test
   fails; make `process_bind_param` pass aware values through → the PG tests
   fail again.
   → verify both, then restore.
7. Full suites + ratchet (pinned, on the commit) + format; the P1 PG suite
   locally green.
8. Commit, push, PR to `dev` linking intent/spec/plan; merge when CI (incl.
   `postgres (P1 acceptance)`) is green.
9. **Post-deploy (next release):** mint a key with an expiry in the portal →
   201; use a key → Settings shows "last used"; resolve a test-target
   credential in a run. Close #1308.

## Tests

```bash
podman run --rm -d --name tf-pg-1308 -p 55432:5432 \
  -e POSTGRES_USER=tfactory_test -e POSTGRES_PASSWORD=tfactory_test \
  -e POSTGRES_DB=tfactory_test postgres:15
TEST_POSTGRES_URL=postgresql+asyncpg://tfactory_test:tfactory_test@localhost:55432/tfactory_test \
  TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/postgres/ -m postgres -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest apps/web-server/tests/test_utc_datetime.py -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q
podman stop tf-pg-1308
```

## Rollback

Revert the commit. Stored data and column types are untouched, so a revert
only brings back the five failing writes; nothing needs migrating either way.

## Deviations (recorded during implementation, same commit as the code)

- **`column_types.py`, not `types.py`.** A module named `types.py` shadows the
  standard library's `types` for anything run with that directory as
  `sys.path[0]` (it broke a script run from `server/database/`). Imports as
  `server.database.column_types` are unaffected either way; the name just
  removes the trap.
- **Step 5 — `alembic check` is non-zero, identically on dev.** It reports
  pre-existing model/migration drift unrelated to timestamps (server defaults
  on `git_credentials.kind` / `test_target_credentials.kind`, three `org_id`
  indexes, two `kms_data_keys` comments, a KMS unique constraint). The
  operation list with this change is byte-identical to dev's, so the change
  adds **no** drift — the criterion is "no additional drift". The existing
  drift is out of scope here (worth its own issue).
- Step 1 confirmed the failure on real Postgres (all four writers raise the
  asyncpg `DataError`), and step 2 confirmed SQLite silently drops a non-UTC
  offset (`12:00+02:00` read back as `12:00`) — so the bug also mis-stored
  instants on SQLite, not only failed on Postgres.
- The web-server tests run pytest-asyncio in strict mode, so the async
  round-trip test carries `@pytest.mark.asyncio` (the directory's convention).
