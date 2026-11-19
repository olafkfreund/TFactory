---
status: draft
issue: 1630
author: olafkfreund
---

# Intent: Criterion-literal gate must not read layout parameters as asserted values

## Problem

factory-lab-go PR #1 on 0.13.3: the gate rejected subtask stdin-prints-2-3-6 twice, saying the criterion states "8", while the PR criterion says "2 3 6". The 8 comes from the requirement "counts right-aligned in width 8" carried in the planner's description: a layout parameter, not an output value. The subtask was then dropped. The planner description itself was not inspected (incident history unverified); the width reading is reproduced from the PR body.

## Proposed outcome

A criterion's width/padding/columns numbers are not treated as values the test must assert; the correct test for the PR body passes the gate. Formatting is still verified by the test's own padded-string assertions; only the gate stops demanding the bare width literal.

## Affected users and systems

Gen-Functional gate (apps/backend/agents/criterion_literals.py); any spec-ingest run whose requirements mention formatting widths.

## Constraints

Smallest correct change; tests that fail without the fix; no change to verdict semantics; other teams edit TFactory in parallel, keep the diff narrow.

## Open questions

None. Decision (owner delegation 2026-11-19): take the recommended option recorded in the issue.
