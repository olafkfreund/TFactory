---
status: approved
issue: 1428
author: olafkfreund
---

# Intent: Clear the two re-opening CodeQL injection alerts at the call site, and lower the ratchet baseline

## Problem

The `-sanitized` CodeQL twins (`.github/codeql/custom-queries/`) only trust
values that pass through a barrier function in their lists. Two request
flows reach a sink without one, so CodeQL reports them even though they are
safe, and they come back as new alerts on every refactor:

- `routes/git.py` worktree create: `name` is checked with an inline
  `re.fullmatch`, then used to build `worktree_path`, which reaches
  `shutil.rmtree` (alert 1893, high, `py/path-injection-sanitized`).
- `routes/files.py` `GET /{project_id}/diff`: `base` is only checked for a
  leading `-`, then becomes a `git diff` argv element (alert 1908, critical,
  `py/command-line-injection-sanitized`).

Both were dismissed as false positives on 2026-10-24. The ratchet baseline
`.github/codeql-baseline.json` still allows 4 critical and 1 high alerts,
so a real one could land without failing the weekly ratchet.

## Proposed outcome

- Both flows go through a validator that CodeQL treats as a barrier, so
  neither alert re-opens.
- `base` on `/diff` gets the same ref allow-list `changelog.py` uses, not just
  a leading-dash check.
- The baseline matches the open alerts today: critical 0, high 0, error 0,
  medium 0.

## Affected users and systems

TFactory web-server (`apps/web-server/server/routes/git.py`, `files.py`),
the CodeQL custom query, and the weekly CodeQL ratchet. No UI caller sends
`/files/{id}/diff` today (the UI uses `/tasks/{id}/worktree/diff`).

## Constraints

- Prefer call-site changes. Touch a `.ql` barrier list only where a call-site
  change cannot clear the alert, and only with a validator that returns the
  checked value and is honest for list-form argv with no shell.
- Do not dismiss alerts or weaken the stock rules.
- No release; this ships with the next one.

## Open questions

None. The other dismissed alerts (1906 changelog, 1907 rg query,
1909 terminal worktree) stay dismissed and out of scope.
