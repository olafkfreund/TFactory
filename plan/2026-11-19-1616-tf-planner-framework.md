---
status: approved
issue: 1616
spec: spec/2026-11-19-1616-tf-planner-framework.md
coders:
  1: lead   # small contained edit, kept by the lead
reviewers:
  - codex
---

# Plan: normalise api to unit for unit-only frameworks

Decisions from the spec: rewrite lane api to unit only for unit-only frameworks; all other lane errors stay rejected.

## Steps

1. apps/backend/agents/planner.py: add `_normalise_single_lane` and call it in `_validate_emitted_plan` next to `_normalise_js_test_paths` → verify by tests/test_planner.py
   Traps: keep Lane import lazy; do not normalise non-api lanes.
2. tests/test_planner.py: add the jest-on-api test → verify by pytest.

## Tests

`.venv/bin/pytest tests/test_planner.py` green; ruff.

## Rollback

Revert the commit.
