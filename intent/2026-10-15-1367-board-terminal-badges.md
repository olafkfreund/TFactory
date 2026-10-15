---
status: draft
issue: 1367
author: olafkfreund
---

# Intent: a failed or empty verify looks finished on the pipeline board

## Problem

The TFactory pipeline board decides a card's badge from the task status
(`apps/frontend-web/src/components/pipeline/TFactoryPipelineBoard.tsx:59-67`):

- `isDone` matches only `triaged`, `triaged_empty`, `evaluated`;
- `isFailed` matches `*_failed`, `stuck`, `stalled`, `replan_needed`.

Two terminal statuses match neither:

- the literal `failed`, written by the planner (`agents/planner.py:875,968`),
  the verify reaper (`agents/verify_dispatch.py:1365`), the startup orphan
  reconcile (`agents/liveness_sweep.py:216`) and a checkout failure
  (`agents/tools_pkg/tools/task_control.py:563`);
- `generated_empty`, written when the plan asked for nothing
  (`agents/gen_functional.py:1250`).

Such a card gets no done or failed badge and its stage chip reads `active`, so
a dead run looks like it is still working. A reaped run (phase
`verify_job_reaped`) also has no status the column mapping knows, so it falls
back through its phase to the Plan column (`:76-82`) although it died in the
verify Job.

The task list's colour helper has the same gap: `statusColor`
(`components/tfactory/TFactoryTaskList.tsx:59-74`) colours `failed` blue,
"in-flight". The board's info line and the lane grid use the same helper.

Found by the Factory#971 terminal-status audit.

## Proposed outcome

- A `failed` card shows the failed badge and a failed stage chip, and is red in
  the task list.
- A `generated_empty` card shows the done badge; the stages that never ran do
  not show as done or active.
- A reaped card sits in the Report column, where its verify ran.

## Affected users and systems

TFactory portal users: the Tests/Tasks pipeline board and task list. Frontend
only; no API change.

## Constraints

- No change to the status values the backend writes.
- Keep `triaged_empty` and the other existing mappings as they are.

## Open questions

None. Choice taken: `generated_empty` is shown as done, the same as
`triaged_empty` (an empty but legitimate result, #1253), not as failed.
