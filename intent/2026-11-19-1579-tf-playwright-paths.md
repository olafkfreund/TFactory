---
status: draft
issue: 1579
author: olafkfreund
---

# Intent: Playwright accepts .spec.js and the repo's testDir

## Problem

aifactory-demo #625 (JS) planner failed with planner_invalid_invalid_test_path_after_retry: tests/e2e/submit-score-through-form.spec.js was rejected because the playwright test_path_conventions only list .spec.ts. The browser lane also only staged *.spec.ts.

## Proposed outcome

A playwright .spec.js/.spec.ts under tests/, e2e/ or the repo's playwright.config testDir passes validation; the staged browser lane runs .spec.js too. The rejection text already reaches the retry prompt (invalid_test_path reminder).

## Affected users and systems

TFactory backend (apps/backend/agents), framework descriptors.

## Constraints

Narrow diff; other teams touch TFactory in parallel. No secrets, no live-cluster changes.

## Open questions

None. Decision (owner delegation 2026-11-19): take the fix recommended in the issue.
