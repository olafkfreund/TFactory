---
status: draft
issue: 1257
author: Olaf Krasicki-Freund
---

# Intent: Stop Alembic migrations raising permanent CodeQL false positives

## Problem

CodeQL's `py/unused-global-variable` fires on `revision`, `down_revision`,
`branch_labels` and `depends_on` in every file under `alembic/versions/`.
Alembic reads these as module attributes through its script loader, so no
Python code ever references them — the alerts are false by construction.

Every new migration adds four. `main` enforces
`required_conversation_resolution`, so each migration PR blocks until someone
dismisses four alerts by hand (#1252 hit this). About 40 such alerts are
already open, which is the volume at which nobody triages the list and a real
finding goes unseen — the argument `.github/codeql/codeql-config.yml` already
makes for `py/path-injection`.

## Proposed outcome

- A new Alembic migration opens a PR with zero `py/unused-global-variable`
  alerts on its migration file.
- The existing alerts of this class on `alembic/versions/` are closed.
- The rule still fires everywhere else in the Python tree.

## Affected users and systems

- `.github/codeql/codeql-config.yml` (CodeQL code-scanning config)
- Anyone landing a DB migration on TFactory
- AIFactory has the same shape; out of scope here, but the fix should be
  copyable.

## Constraints

- Must not suppress any **security** query, and must not widen beyond
  `alembic/versions/`.
- Must not contradict the config's doctrine (every security exclusion paired
  with a barrier-aware replacement). This is a quality rule, which the
  doctrine does not cover — the spec must say so in the config comment.
- The CodeQL config file supports `query-filters` by query id and
  `paths-ignore` for all queries, but no native "this rule, these paths"
  filter. The spec must pick a mechanism that honours the scope above.

## Open questions

- Should `alembic/versions/` be excluded from CodeQL entirely (simplest,
  `paths-ignore`), or only from this one rule (narrower, needs an alert
  filter step or a custom query)? The narrower scope is what the issue asks
  for; the wider one loses security analysis on migration SQL.
