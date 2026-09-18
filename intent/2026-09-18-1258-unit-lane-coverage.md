---
status: draft
issue: 1258
author: Olaf Krasicki-Freund
---

# Intent: Unit-lane coverage must reach the verdict

## Problem

In a measured end-to-end run on the deployed build, the planner emitted seven
`(python, pytest, unit)` subtasks, they ran under pytest, and 13 `coverage.xml`
files were written. Every verdict nonetheless recorded `lane = None`,
`coverage_delta_pct = 0`, `coverage_new_lines = 0`, and the judge wrote
"All are browser-lane (coverage N/A)".

The coverage was collected, paid for, and discarded. One of the Evaluator's
five signals was silently absent from every verdict, and because it read as
`0` rather than "not measured" it looked like a real measurement of "no
change".

This reproduced *after* both deterministic stamps existed on the evaluator
path — `_stamp_verdict_lanes` (#1018) and `_stamp_verdict_coverage` (#1024),
both 2026-08-09; the issue was filed 2026-08-29. So those fixes do not cover
this path, and the reason is not yet known.

## Proposed outcome

- A `(python, pytest, unit)` subtask's verdict carries `lane = "unit"`.
- When that test's `coverage.xml` shows a real delta, the verdict carries a
  non-zero `coverage_delta_pct` — proven by a mutation-checked test (asserting
  the key exists passes today).
- "Coverage not applicable / not measured" is distinguishable from "measured,
  no change" on every verdict (`None` vs `0`), which `_stamp_verdict_coverage`
  already intends.

## Affected users and systems

- `apps/backend/agents/evaluator.py` — `_stamp_verdict_lanes`,
  `_stamp_verdict_coverage`, `_measured_coverage`, `_apply_lane_attribution`
- The evaluator prompt's lane/coverage framing (`prompts/evaluator.md`)
- Downstream: `confidence` scoring, `val_block` VAL levels, the Backstage
  scorecard, `lane_progress` (#1259) — all read the verdict's `lane`
- Deployed cluster runs (https://tfactory.freundcloud.org.uk)

## Constraints

- Must not fabricate coverage: an unmeasured test stays `None`, never `0`.
- Must not regress the browser lane, where coverage genuinely does not apply.
- The fix goes in the shared stamping path, not in one lane's call site.
- Acceptance is mutation-checked, per the issue.

## Open questions

- Root cause is unconfirmed. The leading hypothesis is that
  `_stamp_verdict_lanes` fails to match these verdicts to plan subtasks (every
  verdict `unmatched`), which would also explain #1259's end-of-run `pending`.
  The spec step will reproduce it against a real `verdicts.json` +
  `test_plan.json` pair before designing. If you have the workspace from the
  run in the issue, that is the fastest reproduction.
- Fix #1258 and #1259 together if they share the cause, or keep them as two
  PRs?
