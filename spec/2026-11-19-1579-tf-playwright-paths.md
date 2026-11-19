---
status: draft
issue: 1579
intent: intent/2026-11-19-1579-tf-playwright-paths.md
---

# Spec: Playwright accepts .spec.js and the repo's testDir

## Design

Add .spec.js globs to frameworks/playwright/descriptor.yaml (this also makes the existing plain-JS normaliser turn .ts into .js for playwright). In planner._validate_test_paths, additionally accept <testDir>/**/*.spec.{ts,js} where testDir is read by regex from playwright.config.*; nix_env._stage_browser_specs globs *.spec.js as well as *.spec.ts.

## Alternatives rejected

Full JS parse of playwright.config: heavy, regex covers the literal form. Loosening the validator for all frameworks: out of scope.

## Risks

A computed testDir is not understood and falls back to the conventions. A repo with testDir outside tests/ now accepts specs there only for playwright.

## Verification

tests/test_planner.py: .spec.js accepted; testDir spec accepted; spec outside testDir still rejected and retried with invalid_test_path.
