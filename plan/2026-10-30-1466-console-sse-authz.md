---
status: approved
issue: 1466
spec: spec/2026-10-30-1466-console-sse-authz.md
coders:   # the user asked for agy as a team member this wave
  1: agy
  2: agy
  3: agy
---

# Plan: gate the console SSE stream, record the console actor, check the stdio MCP reads

Paths are relative to `apps/web-server/server/` unless they start with `tests/`.
Reads need `viewer`. A key whose `org_id` differs from the project's `org_id`
is denied always (403, not behind the flag), as `_authorize_key` already does for
writes (#1465). Same-org role checks use the existing shadow/enforce switch
(`RBAC_ENFORCE_TASK_ROUTES`, default off). Nothing changes today.

Repo traps (every step):
- CodeQL: no variable first assigned in a `try` and read in an `except`/after it;
  no function mixing implicit and explicit returns.
- No second DB session/connection in a check path: use the route's `db`.
- Tests patch through the module object the code reads (`proxy_router_mod`,
  `server.routes.projects.load_projects` as the existing fixture does): other tests reload `server.*`.
- Ruff/format as the file already is (TF runs `ruff format --check` in CI).

## Steps

1. `mcp_stdio/router.py`.
   - `_authorize_key(key, project_id, db, minimum_role: str = "member")`: pass
     `minimum_role` to the denial audit and to `ProjectAccessChecker(minimum_role)`.
     Move the project lookup into `def _foreign_projects(key) -> set[str]`: empty
     for `_LegacyAdminKey` or a key without `org_id`, else ids from
     `load_projects()` (same import as today) whose `org_id` is set and
     `!= key.org_id`; a lookup exception re-raises if `_enforcing()` else returns
     an empty set. `_authorize_key` denies when `project_id in _foreign_projects(key)`.
     Keep the existing comment and audit action.
   - `_authorized_key(scope, minimum_role="member")` passes it through.
   - `/tasks/{task_id}`, `/status`, `/logs`, `/worktree/diff`:
     `_=Depends(_authorized_key(MCP_READ_SCOPE, "viewer"))`.
   - Lists: `/projects` drops items whose `"id"` is foreign; `/tasks` rebuilds the
     `TaskList` without foreign `project_id`s (`total` = new length); `/tasks/running`
     rebuilds `RunningTasksResponse` without ids whose prefix before `:` is foreign.
     Name the key `key`, compute `_foreign_projects(key)` once.
   → verify by `cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_mcp_stdio_authz.py ../../tests/test_mcp_stdio_proxy.py -q`

2. Console.
   - `routes/tasks_views.py` SSE route (~79): `dependencies=[Depends(require_task_access("viewer"))]`
     (import `Depends`, `require_task_access` from `.project_authz`).
   - `rmux/bridge.py` attach (~175) and detach (~236): `user = getattr(request.state, "user", None) or {}`;
     `user_id = user.get("id")`, `org_id = user.get("org_id")`.
   - `tests/test_task_route_rbac.py`: add `("tasks_views", "GET", "/{task_id}/agent-console/sse", "viewer")`
     to `EXPECTED`, count 21.
   → verify by `cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_task_route_rbac.py ../../tests/rmux/test_bridge.py -q`

3. Tests.
   - `tests/test_mcp_stdio_authz.py`: org-B key on `/tasks/p1:s1/logs` and
     `/worktree/diff`: 403 with the flag off, handler not called, one
     `authz.task.denied` audit; same-org viewer key: 200; lists `/projects`,
     `/tasks`, `/tasks/running` with an org-B key leave out p1 entries and keep
     a `pB` (org B) entry; legacy token sees all. Give the fixture's keys
     `MCP_READ_SCOPE` too.
   - `tests/rmux/test_bridge.py` (or the file that tests attach): the attach audit
     row has `user_id` and `org_id` from `request.state.user`.
   → verify by step-1 and step-2 commands; then ruff check + format, ratchet
     exactly as CI runs it, full suite on Postgres.

## Deviation (lead's Opus review, 2026-10-30)

CRITICAL: `get_worktree_diff` and `get_worktree_status` (`routes/tasks_worktree.py`) accepted a
bare spec id or an unknown project prefix and then scanned every project for
the spec. `nope:<spec>` and `<spec>` skipped the cross-org check, and the shadow
404 was let through, so an org-B key could read org A's diff with the flag off.
- Root cause: both handlers now require `project_id:spec_id` (a bare id gets 400)
  and look the project up by exact id through `_exact_project_path`. There is no
  spec scan, the same as `merge_worktree` / `create_pr_from_task`.
- Second layer in `mcp_stdio/router.py`: for an org-scoped `acw_` key, a
  `project_id` that is not a known project is always denied, not behind the flag
  (404 "Project not found" / 403, both audited as `authz.task.denied`). A `load_projects()` error fails closed with 503 for org-scoped keys
  (`_key_projects`), on lists too. Previously it failed open in shadow mode.
- Tests, flag OFF: `nope:s` and bare `s` on `/worktree/diff` with a same-org key
  get 404 and the handler is not called; a registry error gets 503; at the
  handler level, a bare id gets 400 and `nope:s1` does not find p1's spec.
- Not changed: `get_worktree_merge_preview` takes a bare spec id by design and
  scans every project. It is not an MCP proxy. Reported to the lead.

## Tests

Full `tests/` suite with the Postgres `DATABASE_URL` CI uses; ratchet:
`python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts`.

## Rollback

Revert the PR. No schema or config change.
