---
status: approved
issue: 1466
intent: intent/2026-10-30-1466-console-sse-authz.md
---

# Spec: gate the console SSE stream, record the console actor, check the stdio MCP reads

## Design

- `routes/tasks_views.py` SSE route: `dependencies=[Depends(require_task_access("viewer"))]`;
  add it to `EXPECTED` in `tests/test_task_route_rbac.py`.
- `rmux/bridge.py` attach/detach: actor from `request.state.user["id"]` / `["org_id"]`.
- `mcp_stdio/router.py`: `_authorize_key` and `_authorized_key` (#1465) take
  `minimum_role` (default `"member"`, so writes are unchanged). Per-task reads use
  `_authorized_key(MCP_READ_SCOPE, "viewer")`: always-on cross-org 403, then
  `ProjectAccessChecker("viewer")` in shadow/enforce.
- Lists (`/projects`, `/tasks`, `/tasks/running`) keep `require_acw_scope` and drop
  entries whose project `org_id` is set and differs from the key's `org_id`
  (`_foreign_projects(key)`, shared with `_authorize_key`'s lookup).

## Alternatives rejected

- `_authorized_key` on lists: with no `project_id` it would check project `""` (404 decision).
- 403 on lists for a foreign key: the issue (PF #958) asks for filtering.

## Risks

Lists hide projects with a foreign `org_id` from org-bound keys; none exist today.

## Verification

`tests/test_mcp_stdio_authz.py` (cross-org read 403 with the flag off, viewer allowed,
list filtering), `tests/test_task_route_rbac.py` inventory, a bridge test asserting
the audit actor; ruff; ratchet as CI runs it; full suite on Postgres.
