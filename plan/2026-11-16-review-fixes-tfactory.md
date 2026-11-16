---
status: approved
issue: 1515
spec: spec/2026-11-16-review-fixes-tfactory.md
coders:
  1: lead (small, three files, hand-written)
reviewers:
  - codex
---

# Plan: three review defects

Decisions: as in the spec (forward run limits; lock only criterion values; condition URL keys on absent host credential, drop GRAPHITI_MCP_URL).

## Steps

1. `apps/backend/agents/verify_dispatch.py` (before the AIFACTORY_BASH_SANDBOX read): forward the vars → `pytest tests/test_verify_dispatch.py`
   Traps: never invent defaults.
2. `apps/backend/agents/gen_functional.py` (lock after the assert step): intersect with `criterion_literals` → `pytest tests/test_gen_functional_two_step.py`
   Traps: fixture description now states 11.76.
3. `apps/web-server/server/utils/project_env.py`: conditional URL keys, drop GRAPHITI_MCP_URL → `cd apps/web-server && pytest tests/test_project_env_allowlist.py`
   Traps: settings route keys must stay allowed.

## Tests

The three files above pass; each new test fails with the source change reverted.

## Rollback

Revert the PR.
