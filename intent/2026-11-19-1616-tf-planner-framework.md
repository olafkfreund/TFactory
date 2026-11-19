---
status: approved
issue: 1616
author: olafkfreund
---

# Intent: planner rejects jest on lane 'api'

## Problem

For aifactory-demo #625 (Node scoreboard) the planner declared framework `jest` on lane `api`; jest supports only `unit`, so `_validate_framework_consistency` rejected it twice and the task died at planner_failed after ~1 USD. The language `typescript` is jest's registry language and is valid; the JS-only repo is already handled by the .ts to .js path rewrite (#1579).

## Proposed outcome

A jest (unit-only) subtask declared on lane `api` is moved to `unit` and the plan is accepted first time.

## Affected users and systems

Planner plan validation (`apps/backend/agents/planner.py`), any HTTP-API test planned with a unit-only framework.

## Constraints

Do not widen other lane rules: a browser lane on kotlin/gradle stays rejected (existing test).

## Open questions

None.
