---
status: draft
issue: 1308
intent: intent/2026-09-18-1308-naive-datetime-columns.md
---

# Spec: Timestamps must survive Postgres — no tz-aware values into naive columns

Decisions carried from intent review: **naive UTC** as the stored convention
(no column-type migration); **a model-level guard** so the SQLite suites see
this class, plus **one Postgres acceptance test**.

## Findings (audit of `apps/web-server/server`, dev after 0.9.26)

- **~33 `DateTime` columns, all naive** (`timestamp without time zone`); the
  issue's "8" undercounted multi-line declarations. Most are
  `server_default=func.now()` and never written from Python.
- **Directly written columns, by writer:**

  | Writer | Column | Value | Status |
  | --- | --- | --- | --- |
  | `routes/api_keys.py:164` | `ApiKey.expires_at` | aware | **broken** — the 500 |
  | `mcp_remote/auth.py:155` | `ApiKey.last_used_at` | aware | **broken** — commit swallowed, never recorded |
  | `services/credential_resolver.py:54` | `TestTargetCredential.last_used_at` | aware | **broken** — `await db.commit()` **raises**: resolving a test-target login credential fails on Postgres (#107 lane logins) |
  | `routes/email.py:339,555` | `EmailAccount.token_expiry` | aware | **broken** — email OAuth connect |
  | `services/email_service.py:210,311` | `EmailAccount.token_expiry` | aware | **broken** — token refresh |
  | `services/job_state_store.py` | `JobState.*_at` | `_now_naive()` | ok — this helper's docstring already describes this exact bug |
  | `routes/oidc_routes.py:210` | `OidcRefreshSession.expires_at` | `.replace(tzinfo=None)` | ok |
  | `crypto/rotation.py:152`, `services/gdpr.py:172`, `services/audit_service.py:215` | `rotated_at`, `gdpr_erased_at`, `retention_until` | `datetime.utcnow()` | ok (naive; `utcnow` is deprecated but correct) |

- The fix was made once, locally (`job_state_store._now_naive`), and never
  generalised — each new writer re-derived the timestamp and five got it wrong.
- Why tests missed it: the suites run SQLite, which stores an aware value in a
  naive column without complaint. `tests/postgres/` (marker `postgres`,
  `TEST_POSTGRES_URL`, run by CI's `postgres (P1 acceptance)` job) exercises
  real PG but has no case for these writers.

## Design

1. **One column type owns the convention.** New
   `server/database/types.py::UTCDateTime`, a SQLAlchemy `TypeDecorator` with
   `impl = DateTime` (naive) and `cache_ok = True`:
   - `process_bind_param`: `None` → `None`; aware → converted to UTC then
     `tzinfo` stripped (correct for any offset, not just `+00:00`); naive →
     passed through unchanged (naive is UTC by convention, matching
     `func.now()` and every existing read).
   - `process_result_value`: unchanged (naive out), so no reader changes.
   The DB column type is still `TIMESTAMP WITHOUT TIME ZONE` → **no Alembic
   migration**, no change to stored data.
2. **Every model `DateTime` column uses it.** All ~33 `mapped_column(DateTime,
   …)` in `server/database/models.py` become `mapped_column(UTCDateTime, …)`.
   This fixes all five broken writers at once, with no call-site edits, and a
   future writer cannot reintroduce the bug.
3. **Call sites stay as they are.** The aware writes become correct by
   construction; rewriting them would be churn without a behaviour change.
   `job_state_store._now_naive()` stays (already correct).
4. **Enforcement:** a test walks `Base.metadata` and fails if any column's type
   is a plain `DateTime` rather than `UTCDateTime` — so a new model column
   cannot silently opt out. This is the "guard" from the intent review.

## Alternatives rejected

- **Fix the five call sites with a helper only** — fixes today's bug; the next
  writer repeats it (as five already did after `_now_naive` existed).
- **Guard that raises on aware values** — in production a timestamp write
  would still fail, just differently; normalising is equally strict about what
  is stored and never breaks the request.
- **Migrate columns to `timestamp with time zone`** — rejected at intent review
  (live-data migration for no functional gain over a correct naive-UTC
  convention).
- **An SQLAlchemy `before_flush` listener** — global, implicit, and blind to
  Core `insert()`/`update()` statements; the column type covers ORM and Core.

## Risks

- **Reads of existing rows:** unchanged — `process_result_value` returns what
  the DB returns (naive).
- **Non-UTC aware values** (e.g. `+02:00`): converted to the correct UTC
  instant before stripping — covered by a test.
- **Alembic autogenerate:** a `TypeDecorator` over `DateTime` renders as
  `DateTime`; verified in the plan that `alembic check`/autogenerate shows no
  diff.
- **Comparisons in Python** (e.g. `_lookup_by_digest` expiry check): unchanged
  — values read back are still naive and it already treats naive as UTC.

## Verification

- **Unit (`UTCDateTime`):** aware UTC → naive same wall time; aware `+02:00` →
  naive UTC instant (2 h earlier); naive → unchanged; `None` → `None`.
- **Guard test:** every `DateTime`-typed column in `Base.metadata` is
  `UTCDateTime` (mutation: reverting one column to `DateTime` fails it).
- **SQLite round-trip:** an `ApiKey` saved with an aware `expires_at` reads back
  naive UTC with the right instant.
- **Postgres acceptance (`tests/postgres/`, the P1 job):** minting a key with an
  expiry via the route succeeds (201) and the row's `expires_at` is the right
  instant; stamping `last_used_at` and the test-target credential resolve both
  commit — **written failing first on PG** (the current aware writes raise
  there).
- `alembic` autogenerate shows no diff; suites + ratchet green.
- **Post-deploy:** mint a key with an expiry in the portal → 201; use a key →
  Settings shows "last used".
