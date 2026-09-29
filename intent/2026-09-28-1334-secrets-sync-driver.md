---
status: approved
issue: 1334
author: olafkfreund
---

# Intent: the postgres-backed acceptance gates run again, in all three services

## Problem

`secrets (P2 acceptance)` in `.github/workflows/ci.yml` has been red on `dev`
since 2026-09-26 and no `ci.yml` run has gone green on `dev` since. Two tests
are dead, one of them the only check that a database dump contains no plaintext
credentials:

```
tests/secrets/test_p2_column_migration.py::test_migration_backfills_plaintext_to_encrypted FAILED
tests/secrets/test_p2_column_migration.py::test_pg_dump_contains_no_plaintext_credentials FAILED
E   ModuleNotFoundError: No module named 'psycopg'
```

Measured in the failing job's log (run of 2026-09-26T15:19, job
`secrets (P2 acceptance)`):

- `+ sqlalchemy==2.1.1` — `apps/web-server/requirements.txt:37` declares
  `sqlalchemy[asyncio]>=2.0.0` with no upper bound, so CI resolved 2.1.
- `+ psycopg2-binary==2.9.13` — `tests/requirements-test.txt:30` declares
  psycopg2 and nothing else for the sync path.
- The traceback enters `sqlalchemy/dialects/postgresql/psycopg.py`, i.e.
  psycopg **v3**, not psycopg2.

So the test helper builds its sync URL by stripping the async driver
(`tests/secrets/test_p2_column_migration.py:81,122,149`:
`url.replace("+asyncpg", "")`), leaving a bare `postgresql://`. Under
SQLAlchemy 2.0 that resolved to psycopg2, which the test requirements install;
under 2.1 the bare scheme resolves to psycopg v3, which nothing installs. The
test never said which driver it wanted, so a transitive default moved under it.

The job is not a required status check, so PRs merged straight through the red
(#1333 did).

**A separate, pre-existing defect found while measuring, not in scope here:**
`apps/web-server/server/crypto/__main__.py:108` strips drivers the same way and
calls `create_engine(sync_url)`, but `apps/web-server/requirements.txt` ships
**no** sync postgres driver at all (only `asyncpg>=0.30.0`). The key-rotation
CLI therefore cannot open a postgres database in the production image, on
either SQLAlchemy version. That is a runtime gap in a security tool and gets
its own issue rather than being folded into this fix.

## The same break exists in three services, not one

Surveyed after the above, because a shared shape is never one repo's (the
`git grep` is on each repo's default branch):

| Repo | Sites stripping the driver | Jobs currently red from it |
| --- | --- | --- |
| TFactory | `tests/secrets/test_p2_column_migration.py` x3 | `secrets (P2 acceptance)` |
| PFactory | same file, x3 | `secrets (P2 acceptance)` (measured on PR #790, 2026-09-28T12:36: `sqlalchemy==2.1.1`, `psycopg2-binary==2.9.13`, same `import psycopg` traceback) |
| AIFactory | same file x3, **plus 7 helpers under `tests/postgres/`** | `secrets (P2 acceptance)` and `postgres (P1 acceptance, PG 15 / PG 16)` — the whole schema-migration suite |
| CFactory | none | none: it declares `psycopg[binary]>=3.2`, so its bare URL resolves to a driver it has |

All three declare `sqlalchemy[asyncio]>=2.0.0` and only `psycopg2-binary` for
the sync path. CFactory is immune by accident, not by design — it named psycopg
v3 for other reasons.

So this is one fix shape applied three times, and in AIFactory it clears two
job families, not one. Dependabot PRs in all three repos are sitting BLOCKED
behind these reds.

**A third, unrelated red found in the same sweep, filed separately:**
AIFactory's `backend (ruff + pytest)` fails at
`docker run quay.io/minio/minio:latest` with
`unauthorized: access to the requested resource is not authorized`, exit 125.
The step's own comment says the tests "would just skip" if MinIO never comes
up, but under `shell: bash -e` a failed pull kills the step instead. Different
cause, different fix.

## Proposed outcome

`secrets (P2 acceptance)` is green on `dev` again, and it is green because the
tests name the driver they need rather than inheriting whichever one SQLAlchemy
defaults to this month. A future SQLAlchemy default change cannot break them
the same way.

## Affected users and systems

- `tests/secrets/test_p2_column_migration.py` in TFactory, PFactory and
  AIFactory, plus AIFactory's seven `tests/postgres/` helpers, and each repo's
  `tests/requirements-test.txt`.
- `.github/workflows/ci.yml`'s `secrets (P2 acceptance)` job in three repos and
  `postgres (P1 acceptance)` in AIFactory.
- Possibly `apps/web-server/requirements.txt`, if the SQLAlchemy range is
  capped — which affects the running service, not just tests.
- Not the production async path: the service uses `postgresql+asyncpg://`
  explicitly and is unaffected by the sync-dialect default.

## Constraints

- **Do not make the gate pass by weakening it.** The dump test must still read
  a real postgres dump and still fail if plaintext appears.
- **A dead gate is not fixed until it is required.** If this job can go red
  unnoticed for two days while PRs merge, the fix is incomplete; making it a
  required check on `dev` is part of the outcome or is explicitly deferred with
  a reason.
- No unpinned upper bounds introduced or removed silently: if the SQLAlchemy
  range changes, the reason is written next to it.
- Prove it by running the tests against a real postgres, not by reading the
  diff.

## Open questions

1. **Name the driver, cap SQLAlchemy, or both?** My recommendation: name the
   driver (`postgresql+psycopg2://`) — it makes the test say what it means and
   holds regardless of version. Capping `sqlalchemy[asyncio]<2.2` as well would
   stop other 2.1 surprises reaching the service, but it is a service-wide pin
   decided on its own evidence, not a side effect of a test fix. I would not
   cap in this change.
2. **Make the job required on `dev`?** It protects a security property and it
   has been silently red. My recommendation is yes, in this PR's wake once it
   is green, via the hub's `apply_branch_protection.sh`.
3. **One PR per repo, or a single sweep?** These are four separate repos with
   strict branch protection, so it is three PRs regardless. My recommendation:
   land TFactory first (where the issue is filed and the evidence is deepest),
   then PFactory, then AIFactory — AIFactory is the largest diff and the one
   with a second, unrelated red in front of it.
4. **Add psycopg v3 to the test requirements instead?** Cheaper diff (one line)
   but it leaves the URL ambiguous, so the next default change moves it again.
   I recommend against.
