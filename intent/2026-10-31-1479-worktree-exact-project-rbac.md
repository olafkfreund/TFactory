---
status: approved
issue: 1479
author: olafkfreund
---

# Intent: worktree resolve writes by exact project id; viewer check on worktree reads

## Problem

`POST /api/tasks/{task_id}/worktree/resolve-uncommitted` and
`/resolve-git-merge` (`apps/web-server/server/routes/tasks_worktree.py`) take a
bare spec id and scan every registered project for it. Their
`require_task_access("member")` check keys on the whole bare id, so while
`RBAC_ENFORCE_TASK_ROUTES` is off a caller from another org can write through them.

`GET .../worktree/merge-preview`, `/status` and `/diff` need the exact project id
since #1466/#1474 but carry no `require_task_access("viewer")`, so they run no
role check at all.

## Proposed outcome

- Both resolve writes need `project_id:spec_id`; a bare id gets 400; the project
  is found by exact id, never by scanning.
- The three GET routes carry `require_task_access("viewer")` (shadow mode like
  the other task routes): a different-org caller is logged as `rbac.would_deny`.

## Affected users and systems

TFactory web-server worktree routes. The web UI already sends `task.id`
(`project_id:spec_id`) to all five routes.

## Constraints

- Same pattern as diff/status/merge-preview (#1474). Shadow mode stays default.
- Do not touch `routes/mcp_copilot.py` (#1480 lane).

## Open questions

None. Decision given: exactly the issue's Fix.
