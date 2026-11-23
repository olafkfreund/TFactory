---
status: approved
issue: 1665
spec: spec/2026-11-23-1665-adopt-design-tokens.md
coders:
  1: agy     # large mechanical CSS/TSX edit across ~110 call sites
reviewers:
  - codex
---

# Plan: TFactory adopts the shared design tokens (#1665)

The spec is the contract. There is one step, given to agy. The lead verifies,
commits, releases and runs the browser check.

## Steps

1. Do everything in the spec, in `apps/frontend-web` and
   `.github/workflows/factory-ui-drift.yml`. The hub source is
   `/mnt/code/Source-home/GitHub/Factory` at `origin/main` (`git show
   origin/main:shared/factory-ui/<file>`); copy byte-exact.
   → verify:
   ```
   npm -w apps/frontend-web run typecheck
   npm -w apps/frontend-web run test
   npm -w apps/frontend-web run lint
   npm -w apps/frontend-web run build
   grep -rn "hsl(var(--" apps/frontend-web/src | wc -l   # 0
   ```
   Also run `python3 <hub>/scripts/check_factory_ui_drift.py` for tfactory
   against the hub at `origin/main` (see the workflow for the arguments).

   Traps:
   - never edit the vendored files;
   - keep the theme store's Light/Dark/System behaviour;
   - no layout or spacing changes;
   - lint stays at 0 errors.

## Tests

As the step lists.

## Rollback

Revert the PR. The old theme returns.
