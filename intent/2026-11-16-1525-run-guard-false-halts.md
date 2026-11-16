---
status: draft
issue: 1525
author: olafkfreund
---

# Intent: Run guard false halts

## Problem

The run guard ported in TFactory#1515 and PFactory#1013 (`agents/run_guard.py`) halts healthy runs in two ways:

1. `same_tool_failure` counts consecutive failures per tool NAME. Eight failing Bash calls with different commands halt the session, although failing test runs are normal iteration in test generation. AIFactory's original (#2033) shares the by-name counter but builds a fresh controller per turn; the ports keep one per client, so the counter spans the whole session.
2. TFactory's token ceiling reads the cumulative usage persisted in `status.json`, which is never reset on a re-run. A task that crossed 10M tokens once can never be verified again unless the ceiling is raised or its usage cleared by hand. PFactory has the same flaw: `PlanSession.usage` accumulates across `process()` re-runs and `record_usage` checks the cumulative total.

TFactory#1524 now forwards the guardrail env into verify Jobs, so both defects are about to be live.

## Proposed outcome

- Distinct failing commands never trip the same-tool halt; a true loop (the same tool with the same input failing repeatedly) still halts the session.
- The token ceiling bounds one run's spend, not the task's lifetime spend. A task over the ceiling in an earlier run can be verified or re-planned again.

## Affected users and systems

TFactory verify Jobs, PFactory plan runs. Persisted usage totals stay cumulative for cost reporting.

## Constraints

- Keep the env switches and defaults unchanged.
- Keep diffs tight; regression tests must fail before the fix and pass after.
- Bases are TFactory `dev` and PFactory `dev`.

## Open questions

None.
