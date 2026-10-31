---
status: approved
issue: 1480
author: olafkfreund
---

# Intent: copilot MCP endpoint fails closed without its token

## Problem

`POST /mcp` (`apps/web-server/server/routes/mcp_copilot.py`) is guarded only by
the shared bearer `COPILOT_MCP_TFACTORY_TOKEN`. When the variable is unset the
endpoint lets every caller through, including the write-back tool
`tfactory_report_result`. The token is compared with `!=` (not constant time),
and the tools resolve a bare spec id with `_find_spec_dir`, which scans every
project's `specs/` directory and returns the first match.

## Proposed outcome

- Token unset: every `/mcp` request gets 503; nothing is served.
- Token set: compared in constant time; wrong or missing gives 401 as today.
- Tools take `task_id` as `project_id:spec_id`; a bare id gets 400. No
  cross-project scan remains.

## Affected users and systems

TFactory web-server; any Copilot cloud agent configured with this MCP server.
Prod (factory-gitops) does not set `COPILOT_MCP_TFACTORY_TOKEN` (name grep), so
the endpoint will answer 503 in prod after this lands until the token is set.

## Constraints

- Do not touch `routes/tasks_worktree.py` (another lane, #1479).
- No gitops change, no secret values read or written.

## Open questions

None. Decided by the user: fail closed with 503 (no conditional mounting),
`hmac.compare_digest`, `project_id:spec_id` with bare id → 400.
