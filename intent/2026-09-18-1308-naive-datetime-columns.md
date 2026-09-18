---
status: draft
issue: 1308
author: Olaf Krasicki-Freund
---

# Intent: Timestamps must survive Postgres — no tz-aware values into naive columns

## Problem

On the deployed 0.9.26 (Postgres), minting an API key with an expiry fails
with HTTP 500: `POST /api/keys` computes `expires_at` as a timezone-aware
datetime and the `api_keys.expires_at` column is `timestamp without time
zone`; asyncpg rejects the parameter (`can't subtract offset-naive and
offset-aware datetimes`). Confirmed from the pod log on 2026-09-18.

It is a class, not a line:

- Every `DateTime` column in `server/database/models.py` is naive (8 naive,
  0 `DateTime(timezone=True)`), while writers use `datetime.now(UTC)` /
  `datetime.now(timezone.utc)`.
- A second confirmed case: `mcp_remote/auth.py:155` stamps `last_used_at`
  with an aware value on **every** key use (REST `api:full` and MCP). That
  commit is best-effort and swallowed at DEBUG, so auth still succeeds — but on
  Postgres `last_used_at` is most likely never recorded, and Settings shows
  every key as unused. Silent, which is worse than the loud 500.
- Further candidates of the same shape are not yet confirmed to reach a naive
  column: `services/credential_resolver.py:54` (`last_used_at`),
  `routes/oidc_routes.py:203` (refresh-session expiry), `routes/email.py` and
  `services/email_service.py` (token expiry).

Why it shipped: the test suites run on SQLite, which accepts an aware value in
a naive column, so nothing ever exercised the Postgres behaviour.

## Proposed outcome

- A key minted with an expiry returns 201 on Postgres, and expires when it
  should.
- `last_used_at` is actually recorded on Postgres after a key is used.
- Every DB write of a datetime in the web server is consistent with its
  column, including the unconfirmed candidates above (each either fixed or
  shown not to reach a naive column).
- A test would fail if an aware datetime were written to a naive column
  again — i.e. the SQLite blind spot is closed for this class, not just for
  these lines.

## Affected users and systems

- `apps/web-server/server/routes/api_keys.py`, `mcp_remote/auth.py`, and the
  candidate writers listed above; `server/database/models.py` (column types)
- Portal Settings → API Tokens (expiry field; "last used" column)
- Every agent/CI integration that wants short-lived `acw_` keys
- The Postgres-backed deployment on the p510 cluster

## Constraints

- No data loss or reinterpretation of existing rows: existing naive values
  are UTC by convention (`_lookup_by_digest` already treats naive as UTC).
- If the chosen fix changes column types, it needs an Alembic migration that
  is safe on the live database (and keeps #1257's `__all__` convention).
- Must not weaken expiry enforcement (`_lookup_by_digest`'s comparison).
- The fix must be proven on Postgres, not only SQLite.

## Open questions

1. **Convention:** write naive UTC everywhere (strip tzinfo at the write
   boundary — no migration; matches the existing "naive = UTC" reads), or
   migrate the columns to `timestamp with time zone` (aware end to end; a
   migration on live data)? Recommend **naive UTC at the write boundary** —
   smaller, no live migration, consistent with how reads already behave — plus
   one shared helper so the convention lives in one place.
2. **How to prove it:** add a Postgres-backed test for these writes (CI has a
   `postgres (P1 acceptance, PG 15)` job), or a SQLAlchemy-level guard that
   rejects aware values for naive columns in any backend? Recommend the
   **guard** (a `TypeDecorator` or before-flush check used by the models) so
   SQLite tests fail the same way Postgres does, plus one Postgres acceptance
   case for the key-with-expiry path.
