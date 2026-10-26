---
status: approved
issue: 1431
spec: spec/2026-10-26-1431-contract-schema-enforce.md
coders:
  1: codex   # lane brief: implement steps with codex-implement, fall back to self
  2: codex
---

# Plan: enforce the task-contract schema at ingest by default (#1431)

Decision (approved): `TFACTORY_CONTRACT_SCHEMA_MODE` defaults to `enforce`.
`warn` stays as the explicit opt-out; any unknown value still enforces. A
request without a contract is unchanged. Errors name field paths and validators
only (#718). factory-gitops sets no value, so the cluster enforces after release.

## Steps

1. `apps/backend/agents/tools_pkg/tools/task_control.py` ~L474 and ~L514: default
   `"warn"` → `"enforce"` in the `os.environ.get` call and the docstring
   (`default ``enforce```). `tests/test_spec_ingest_workspace.py` ~L557:
   rename `test_invalid_contract_warned_by_default` to
   `test_invalid_contract_rejected_by_default` (env unset → `ValueError`
   matching "phases", spec dir not created), and add
   `test_invalid_contract_warned_in_warn_mode` (env `warn` → contract stored,
   a warning mentions "phases").
   → verify: `cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_spec_ingest_workspace.py -q`
   Traps: keep the strip/lower; do not change the "any unknown value fails closed" branch.
2. `docs/capabilities-and-usage.md` L204: "`warn` today, `enforce` later" →
   "`enforce` by default; `warn` logs and continues". `CHANGELOG.md` under
   `## Unreleased`: one entry for the flip, with the rollback env.
   → verify: `git grep -n "default \`\`warn\`\`\|warn\` today" -- apps docs` is empty.
   Traps: do not edit older CHANGELOG sections or the 2026-10-24 artifacts; they are history.

## Tests

`tests/test_spec_ingest_workspace.py` passes; the renamed default test fails on
the old code. Pre-commit critical lane passes. PR CI green.

## Rollback

Revert the PR, or set `TFACTORY_CONTRACT_SCHEMA_MODE=warn` on the deployment and restart.
