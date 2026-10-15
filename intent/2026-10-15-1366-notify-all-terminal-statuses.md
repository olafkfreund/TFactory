---
status: approved
issue: 1366
author: olafkfreund
---

# Intent: every terminal verify status reaches CFactory

## Problem

CFactory learns that a TFactory verify has ended only from the completion event
(`completion_outbox` and the webhook). That event is sent by
`triager._notify_completion` (`apps/backend/agents/triager.py:1019`), and the
only callers are the Triager's own status writer (`triager.py:425-429`) and the
hand-back route (`apps/web-server/server/routes/handback.py:133`).

Every run that ends before the Triager therefore ends silently. CFactory's work
item stays pending forever. The statuses that do this today:

| Status | Written by |
| --- | --- |
| `planner_failed` | `agents/planner.py:639,664,687,737,1075,...` |
| `failed` | planner (`planner.py:875,968`), verify reaper (`verify_dispatch.py:1365`), orphan reconcile (`liveness_sweep.py:216`), checkout failure (`tools_pkg/tools/task_control.py:563`), liveness watchdog on a stalled inline stage (`agents/liveness.py:213-230`, added at review) |
| `gen_functional_failed` | `agents/gen_functional.py:1213,1291,1328,1360` |
| `generated_empty` | `agents/gen_functional.py:1250` |
| `evaluator_failed` (in-pod only) | `agents/evaluator.py:2395,2405,2975,3282`, and the crash anchor `workspace_status.py:121-128` |

The Triager's own crash is also silent: when `run_triager` raises past its own
`except`, the anchor writes `triager_failed` through the shared writer
(`workspace_status.py:121-128`), which never notifies.

Found by the Factory#971 terminal-status audit.

## Proposed outcome

Whatever stage ends a verify, CFactory receives exactly one completion event
for that end state, with a `failure` outcome for the failure statuses. A run
that continues after a status (for example `evaluator_failed` in the deployed
`kubejob` mode, where the Triager still runs) does not send an early event.

## Affected users and systems

- TFactory backend agents (planner, gen-functional, evaluator, triager), the
  verify reaper and the startup orphan reconcile in the web-server.
- CFactory completion intake: it starts receiving failure events it never saw.
- Deployed mode: `TFACTORY_VERIFY_EXEC=kubejob`, `APP_LIVENESS_SWEEP_ENABLED=true`.

## Constraints

- Notification stays best-effort: it must never raise into a stage or the reaper.
- No second terminal event for a run that the Triager will still finish.
- The completion envelope format is unchanged (`_build_completion_envelope`
  already maps every non-`triaged` status to `failure`).
- No change to CFactory.

## Open questions

None. Conservative choice taken: `evaluator_failed` is notified only on the
in-pod path, where nothing follows it.
