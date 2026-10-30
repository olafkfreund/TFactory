---
status: approved
issue: 1466
author: olafkfreund
---

# Intent: gate the console SSE stream, record the console actor, check the stdio MCP reads

## Problem

- `routes/tasks_views.py` `GET /{task_id}/agent-console/sse` streams the live
  agent console for any task id with no project check.
- `rmux/bridge.py` attach/detach write `console.attach` / `console.detach` with
  `request.state.user_id` / `org_id`, which `TokenAuthMiddleware` never sets
  (it sets only `request.state.user`), so the rows never name the actor.
- Same gap as PFactory #958: the stdio MCP read proxies in
  `mcp_stdio/router.py` (task, status, logs, worktree diff, project/task/running
  lists) are gated only by the `mcp:read` scope, so an org-B `acw_` key can read
  org-A logs and diffs.

## Proposed outcome

- The SSE route needs `viewer` on the task's project (shadow/enforce, `RBAC_ENFORCE_TASK_ROUTES`).
- Console audit rows carry the caller's id and org.
- A key for another org gets 403 on per-task MCP reads always (not behind the
  flag), and list reads leave out other orgs' projects/tasks. Same-org reads run
  the `viewer` check in shadow/enforce mode.

## Affected users and systems

TFactory web-server: portal console viewers, MCP stdio clients. Nothing changes
today: projects carry no `org_id`, the flag is off.

## Constraints

- Must not lock out the legacy admin token or same-org keys.
- No second pooled connection per check; CodeQL-clean; ratchet clean.

## Open questions

None.
