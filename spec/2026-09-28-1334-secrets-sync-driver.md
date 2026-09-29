---
status: draft
issue: 1334
intent: intent/2026-09-28-1334-secrets-sync-driver.md
---

# Spec: the postgres acceptance gates name the driver they need

## What the measurements settled

| Question | Measured answer |
| --- | --- |
| Is it still broken? | **Yes.** Every `ci.yml` run on TFactory `dev` that actually executed jobs reports `secrets (P2 acceptance)` as `failure`, back to 2026-09-26. (A newer run reads `action_required` with **0 jobs** — an empty job list is not a pass, and reading it as one is how this looked fixed.) |
| What breaks it? | `sqlalchemy==2.1.1` resolved from an unpinned `sqlalchemy[asyncio]>=2.0.0`. Under 2.1 a bare `postgresql://` URL resolves to **psycopg v3**; the test requirements install `psycopg2-binary` only, so the dialect import raises `ModuleNotFoundError: No module named 'psycopg'`. |
| How many places? | **13 call sites in 3 repos.** TFactory and PFactory: 3 each in `tests/secrets/test_p2_column_migration.py`. AIFactory: 3 there plus **7** helpers under `tests/postgres/`. CFactory is immune — it declares `psycopg[binary]>=3.2`. |
| Is production affected? | **Separately, yes, and worse** — `apps/web-server/requirements.txt` ships no sync driver at all, so the key-rotation CLI cannot open postgres on either SQLAlchemy version. Filed as #1336; out of scope here. |

## Design

### 1. The sync URL names psycopg2 explicitly

Every site that today writes

```python
sync_url = url.replace("+asyncpg", "")
```

instead produces `postgresql+psycopg2://…`. The test requirements already install
psycopg2-binary, so this states what was previously left to SQLAlchemy's default
— and a future default change cannot move it again.

A single helper per repo rather than 3 (or 10) inline replacements: the repos
each get one `_sync_url(url: str) -> str` next to the other helpers in the
module that already owns the pattern, and the `tests/postgres/` helpers in
AIFactory call the same one. One definition is also one place to fix if the
driver choice ever changes.

### 2. The dependency range is left alone

`sqlalchemy[asyncio]>=2.0.0` stays unpinned. The break was never SQLAlchemy
being wrong; it was the test not saying what it meant. Capping the range is a
service-wide decision with its own evidence (what else 2.1 changes, what the
runtime needs), and making it a side effect of a test fix would hide that
decision inside an unrelated PR. Recorded here as a deliberate non-change, not
an oversight.

### 3. The gate becomes required

`secrets (P2 acceptance)` is not a required status check, which is why #1333 and
several Dependabot PRs merged straight through it while it was red for three
days. Once green in all three repos it is added to the required set via the
hub's `scripts/apply_branch_protection.sh`, alongside the checks already
declared there.

This is the part that makes the fix durable: a security gate that can go red
unnoticed for three days is not a gate. It lands **after** the three fixes are
merged, or it blocks its own repairs.

## Alternatives rejected

- **Add psycopg v3 to the test requirements.** One line per repo and the tests
  pass — but the URL stays ambiguous, so the next default change moves it again,
  and both drivers then ship in the test image.
- **Cap `sqlalchemy[asyncio]<2.2`.** Fixes the symptom fleet-wide in one line and
  freezes three services on a major version to avoid naming a driver in a test.
  It also leaves the ambiguity in place for whenever the cap is lifted.
- **Make the production requirements carry psycopg2** so the bare URL resolves.
  Ships a driver into the runtime image to satisfy a test, and #1336 shows the
  production path needs a different answer anyway.
- **Mark the tests xfail / skip until resolved.** Turns a red gate into a green
  one that measures nothing — the failure mode this fleet keeps finding.

## Risks

- **13 sites across 3 repos, each with its own CI.** A partial landing leaves
  some repos red; the plan lands them one repo at a time, verifying each before
  the next, and the gate-required step comes last.
- **AIFactory's `tests/postgres/` helpers are not identical** to the secrets one
  (they take and return the URL differently). Assuming they are is how a sweep
  breaks a suite it did not read; each is read before it is changed.
- **Making the gate required changes merge behaviour** for every open PR in
  three repos — including Dependabot's, which do not self-rebase under strict
  protection. Expect a queue to drain afterwards.
- **A green gate proves only that the two tests run.** They must still fail when
  the property fails, which the verification below checks by mutation rather
  than assuming.

## Verification

Deterministic, per repo:

1. `_sync_url` returns a `postgresql+psycopg2://` URL for an asyncpg URL, and
   leaves a URL that already names a sync driver unchanged.
2. No call site still strips `+asyncpg` — a repo-wide grep is recorded in the PR
   with its output, not asserted in prose.
3. The two secrets tests, and in AIFactory the seven `tests/postgres/` modules,
   pass against a real postgres service.

Live, against a real postgres:

4. `secrets (P2 acceptance)` is green in CI for each repo, and in AIFactory
   `postgres (P1 acceptance, PG 15 / PG 16)` too — the job families that are red
   today.
5. **Mutation:** with the fix in place, write a plaintext credential the
   migration should have encrypted and confirm
   `test_pg_dump_contains_no_plaintext_credentials` **fails**. A security gate
   that cannot fail is not restored, and this is the specific test that has been
   dead for three days.
6. After the gate is required, a PR that would previously have merged through a
   red `secrets (P2 acceptance)` is blocked — checked by reading the branch
   protection's required-checks list back from the API, not from the script's
   own output.

Gates per repo: ruff, `ruff format --check` over that repo's CI path list,
`ratchet_lint.py --base origin/<default>` with its `--package` flags, and the
full suite.
