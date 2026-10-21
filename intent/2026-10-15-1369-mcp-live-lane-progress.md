---
status: approved
issue: 1369
author: olafkfreund
---

# Intent: MCP `task_status` reports real lane progress after an Evaluator failure

## Problem

`lane_progress` is seeded all-`pending` when a task is created
(`apps/backend/agents/tools_pkg/tools/task_control.py:564`). The Evaluator
writes the real per-lane values only at the end of a successful evaluation.
When the Evaluator fails (`apps/backend/agents/evaluator.py:2395,2405,2975,3282`),
that write never happens, so the stored value stays all-`pending`.

The portal detail view does not read the stored value. It derives the value on
every read through `derive_lane_progress`
(`apps/web-server/server/routes/tfactory_tasks.py:281-295`, #1259), so it shows
`executed` or `error`. The MCP `task_status` tool (`task_control.py:970-981`)
returns `status.json` as stored, so an agent or CFactory polling over MCP sees
every lane `pending` for a run whose lanes ran.

The #1259 fix was scoped to the portal (its plan, lines 131-134). Neither #1161
nor #1259 covered the MCP reader.

Found by the Factory#971 terminal-status audit.

## Proposed outcome

MCP `task_status` returns the same `lane_progress` as the portal detail view
for the same spec. That holds after `evaluator_failed` and at every other
point in a run.

## Affected users and systems

- MCP clients of the TFactory server (agents, the CFactory cockpit through
  MCP).
- Backend only. The stored `status.json` is unchanged.

## Constraints

- The tool must still never fail because of the derivation. If it fails, the
  tool serves the stored value, as the portal does.
- The response shape does not change.

## Open questions

None.
