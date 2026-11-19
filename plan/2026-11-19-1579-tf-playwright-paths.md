---
status: approved
issue: 1579
spec: spec/2026-11-19-1579-tf-playwright-paths.md
coders:
  all: lead  # small contained edits; the lead wrote them, Codex reviews
reviewers:
  - codex  # independent read-only review of the final diff
---

# Plan: Playwright accepts .spec.js and the repo's testDir

Carries the spec design unchanged: Add .spec.js globs to frameworks/playwright/descriptor.yaml (this also makes the existing plain-JS normaliser turn .ts into .js for playwright). In planner._validate_test_paths, additionally accept <testDir>/**/*.spec.{ts,js} where testDir is read by regex from playwright.config.*; nix_env._stage_browser_specs globs *.spec.js as well as *.spec.ts.

## Steps

1. frameworks/playwright/descriptor.yaml:48-51: add three .spec.js globs -> verify by tests/test_framework_registry.py
   Traps: none
2. apps/backend/agents/planner.py _validate_test_paths: add _matches_conventions; playwright_test_dir lives in nix_env.py (shared with staging, avoids a lazy import) -> verify by tests/test_planner.py
   Traps: ruff PLR0911 (do not add returns to _validate_test_paths)
3. apps/backend/agents/nix_env.py _stage_browser_specs: glob *.spec.js too and stage the playwright.config testDir tree (deviation from first draft, per Codex review) -> verify by tests/test_browser_verdict_signal.py, tests/test_nix_env.py
   Traps: none

## Tests

`apps/backend/.venv/bin/python -m pytest <touched test files>` passes; new tests fail without the fix. Run scripts/ratchet_lint.py for apps/backend and apps/web-server.

## Rollback

Revert the commit; no data or config migration.
