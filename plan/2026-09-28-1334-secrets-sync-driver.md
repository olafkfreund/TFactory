---
status: draft
issue: 1334
spec: spec/2026-09-28-1334-secrets-sync-driver.md
---

# Plan: the postgres acceptance gates name the driver they need

## Approved decisions (self-contained)

- **Why.** `secrets (P2 acceptance)` has failed on every TFactory `dev` run that
  executed jobs since 2026-09-26, and the same break is red in PFactory and
  AIFactory. One of the two dead tests is
  `test_pg_dump_contains_no_plaintext_credentials` — the only check that a
  database dump carries no plaintext credentials.
- **Cause.** `sqlalchemy[asyncio]>=2.0.0` is unpinned, CI resolved
  `sqlalchemy==2.1.1`, and under 2.1 a bare `postgresql://` URL resolves to
  **psycopg v3** while the test requirements install `psycopg2-binary` only →
  `ModuleNotFoundError: No module named 'psycopg'`.
- **Fix.** Name the driver: build the sync URL as `postgresql+psycopg2://`
  through **one helper per repo**, rather than 13 inline `url.replace("+asyncpg", "")`
  call sites. One definition is also one place to change if the driver ever does.
- **Scope: 13 sites in 3 repos.** TFactory and PFactory 3 each in
  `tests/secrets/test_p2_column_migration.py`; AIFactory those 3 **plus 7**
  helpers under `tests/postgres/`. CFactory is immune (it declares
  `psycopg[binary]>=3.2`).
- **The SQLAlchemy range is deliberately left unpinned.** The break was the test
  not saying what it meant, not the library being wrong. Capping is a
  service-wide decision with its own evidence and must not ride in on a test fix.
- **The gate becomes required, last.** It is not a required check today, which
  is how #1333 and several Dependabot PRs merged straight through it while it
  was red for three days. It is added to the required set only once all three
  repos are green, or it blocks its own repairs.
- **Production is a separate defect** (#1336): the web-server image ships no
  sync driver at all, so the key-rotation CLI cannot open postgres on either
  SQLAlchemy version. Not in scope here.

## Steps

One repo at a time, verifying each before starting the next.

1. **TFactory** — add `_sync_url()` beside the existing helpers in
   `tests/secrets/test_p2_column_migration.py`; route all 3 call sites through
   it.
   → verify: unit test that an asyncpg URL becomes `postgresql+psycopg2://` and
   a URL already naming a sync driver is unchanged; `grep -rn 'replace("+asyncpg"'`
   over the repo returns only `apps/web-server/server/crypto/__main__.py`
   (production, #1336), with the output pasted into the PR.
2. **TFactory CI** — `secrets (P2 acceptance)` green on the PR.
   → verify: read the job's conclusion from the run that actually executed jobs.
   A run with **0 jobs** is not a pass (that is how this looked fixed on 09-29).
3. **PFactory** — the identical change, same helper shape, same greps.
4. **AIFactory** — the same, plus the **7** `tests/postgres/` helpers. Each is
   read before it is changed: they take and return the URL differently from the
   secrets one, and assuming they are identical is how a sweep breaks a suite it
   never read.
   → verify: `postgres (P1 acceptance, PG 15)` and `(PG 16)` green as well as
   `secrets (P2 acceptance)` — three job families, not one.
5. **Mutation, once green** (the step that proves the gate is restored, not just
   quiet): write a plaintext credential the migration should have encrypted and
   confirm `test_pg_dump_contains_no_plaintext_credentials` **fails**. Record
   the failing output in the PR, then revert the mutation.
6. **Make the gate required** — hub `scripts/apply_branch_protection.sh`, all
   three repos, after every fix has landed.
   → verify: read the required-checks list back from the GitHub API, not from
   the script's own output.
7. **Close #1334**, linking the three PRs and the mutation evidence.

## Tests

```sh
# per repo, from the repo root
apps/backend/.venv/bin/python -m pytest tests/secrets -q          # TFactory, PFactory
apps/backend/.venv/bin/python -m pytest tests/secrets tests/postgres -q   # AIFactory
apps/backend/.venv/bin/ruff check apps/backend apps/web-server tests scripts
apps/backend/.venv/bin/ruff format --check <that repo's CI path list>
apps/backend/.venv/bin/python scripts/ratchet_lint.py --base origin/<default> \
  --package apps/backend --package apps/web-server --package scripts
```

Note for AIFactory: it has **two** test roots — `tests/` and the co-located
`apps/backend/test_*.py`, which CI runs as a separate step. Both are run before
its PR is opened.

Expected: the secrets tests fail before the change with
`ModuleNotFoundError: No module named 'psycopg'` and pass after; the dump test
fails under the step 5 mutation.

## Rollback

Revert the three PRs; the tests return to relying on SQLAlchemy's default
dialect and go red again. If the gate was already made required, remove it from
the required set in the same change — leaving a required check that its own
revert breaks would block every PR in three repos.
