---
status: draft
issue: 1342
intent: intent/2026-10-21-1342-liveness-sweep-deadline.md
---

# Spec: name the deadline and max idle in the liveness sweep summary

## Design

In `run_one_sweep` resolve the effective deadline the same way the predicate
does (`deadline_seconds` if given, else `agents.liveness._deadline_seconds()`),
take the largest `idle_seconds` across the verdicts (0 when no active task
reported one), and log:

`liveness sweep: 6 task(s) checked, 0 stalled (deadline 900s, max idle 480s)`

## Alternatives rejected

- "N idle beyond 900s deadline": wrong when a Job-active skip is idle past the
  deadline but not stalled; "stalled" stays the counted thing.
- Passing the deadline back from `sweep()`: wider API change for a log line.

## Risks

Log format change; nothing parses this line (grep shows no consumer).

## Verification

A test captures the INFO record and asserts the deadline and max idle appear;
it fails on the old format.
