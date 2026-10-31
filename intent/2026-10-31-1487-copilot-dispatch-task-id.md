---
status: draft
issue: 1487
author: olafkfreund
---

# Intent: Copilot dispatch prompt names the MCP task id

## Problem

`apps/backend/agents/copilot_dispatch.py` tells Copilot to call the
`tfactory_get_*` MCP tools, but neither the `@copilot` prompt comment nor the
issue body gives it the task id. Since #1485 those tools need
`project_id:spec_id`; anything else answers 400. So Copilot cannot call them
even when `COPILOT_MCP_TFACTORY_TOKEN` is set.

## Proposed outcome

The prompt comment and the created issue body both contain
`project_id:spec_id` and say to pass it as the `task_id` argument of the
`tfactory_get_*` tools.

## Affected users and systems

TFactory backend `agents/copilot_dispatch.py`. Copilot cloud agent runs
dispatched from TFactory. Nothing is live today: prod does not set the token.

## Constraints

- `dispatch_test_writing` has no project id in scope. Do not parse it out of
  `spec_dir` paths.
- The id must pass `mcp_copilot._resolve_spec_dir`'s slug check.

## Open questions

None.
