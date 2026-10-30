---
status: approved
issue: 1458
intent: intent/2026-10-30-1458-rbac-task-route-gaps.md
---

# Spec: close the task-route RBAC gaps before S6

## Design

### 1. One shared decision, connection released on every path

`routes/project_authz.py`: move the body of `ProjectAccessChecker._authorize`
into a public method `check(user, project_id, db) -> dict[str, Any]` that
takes the principal directly, so callers without `request.state.user` (the
mcp_stdio proxies, the console WebSocket) reuse the exact shadow/enforce
logic. `_authorize` becomes `return await self.check(request.state.user, ...)`.

Inside `check`, the call to `authorize_project_for_user` is wrapped in its
own `try/finally` whose `finally` does
`with contextlib.suppress(Exception): await db.rollback()`. The connection
is therefore back in the pool before the handler, the denial audit
(`log_audit_event_bg`, its own session) or the re-raise runs. The existing
deny-path rollback becomes redundant and is removed. `enforce` stays
assigned before the `try`, and `check` returns the principal on every path
(`cast`), so no uninitialized name in an `except` and no mixed returns.

### 2. Routes the dependency did not reach

| Surface | Route | Role | How |
|---|---|---|---|
| `routes/projects.py` | `POST /{project_id}/tasks` | member | `dependencies=[Depends(ProjectAccessChecker("member"))]` |
| | `POST /{project_id}/tasks/{spec_id}/logs/watch`, `/unwatch` | viewer | same, `"viewer"` |
| | `POST /{project_id}/tasks/archive`, `/unarchive` | member | same |
| `routes/tasks_logs.py` | `POST /{task_id}/logs/watch`, `/unwatch` | **viewer** (was member) | existing dependency |
| `rmux/bridge.py` | `POST /{spec_id}/agent-console/attach`, `/detach` | member | `await ProjectAccessChecker("member").check(request.state.user, project_of(spec_id), db)` first in the handler |
| | `WS /{spec_id}/agent-console/ws` | viewer (read-only stream; typing needs attach = member) | `authenticate_websocket` replaces `verify_websocket_token`; principal = JWT claims, or `{"is_service": True}` for the legacy token / auth disabled; `check` on a short-lived `async_session_factory()` session; an enforced denial closes with 4003 |
| `mcp_stdio/router.py` | create-and-run (`project_id` query), start, stop, recover, approve-plan, create-pr, merge | member | new `_authorize_key(key, project_id, db)`; proxies gain `db = Depends(get_db)` |

`project_of(spec_id)` is `spec_id.split(":", 1)[0]`, the same rule
`TaskAccessChecker` uses. The frontend (`AgentConsole.tsx`) sends the
composite task id.

`ProjectAccessChecker` is used directly; TF has no `require_project_access`
factory and adding one buys nothing.

### 3. mcp_stdio principal and the cross-org check

Principal: `_LegacyAdminKey` → `{"id": "default", "is_service": True}` (same
as the REST middleware's legacy-token principal); `AuthenticatedKey` →
`{"id": key.user_id, "role": "user", "api_key_id": key.key_id,
"org_id": key.org_id}` (same shape as the middleware's `acw_` principal).

**Decision: the cross-org key check is always enforced; the rest follows the
flag.** `_authorize_key` first loads the project; if the key has an
`org_id`, the project has an `org_id` and they differ, it audits
`authz.task.denied` and raises 403 whatever `RBAC_ENFORCE_TASK_ROUTES` says.
Then it runs `ProjectAccessChecker("member").check` (shadow or enforced).

Why: a key minted in org A acting on org B is a plain authorization bug,
not a role-model rollout question. It can only fire once a project has an
`org_id`, which no TF project has today, so nothing breaks now; but S6's
`org_id` backfill may land before the flag flip, and that window must not
open a cross-org hole. The rest of the check (unowned project → 403, no
membership → 403, role below member) would deny every key today because
projects have no `org_id`, so it must stay in shadow until S6.

## Alternatives rejected

- **Flag-gate the whole mcp_stdio check.** Leaves a cross-org write hole
  between S6's backfill and the flag flip.
- **Always enforce the whole mcp_stdio check.** Every key call 403s today
  (projects have no `org_id`).
- **Rename the bridge path param to `task_id` and use `TaskAccessChecker`.**
  Changes handler signatures and the route templates other code may match;
  the inline `check` call is the same logic.
- **A second session for the membership query (PF #950 first cut).** Two
  pooled connections per request; the `finally` rollback on the request
  session is enough.
- **Leave log watch/unwatch at member.** Viewers could not watch logs after
  S6; PF #950 chose viewer.

## Risks

- A bare (non-composite) spec id on the console routes resolves no project:
  shadow logs a would-deny; after S6 it is refused. The UI sends the
  composite id.
- `db.rollback()` before the handler: if a handler relied on a transaction
  begun in the dependency it would lose it. None does; the dependency only
  reads.
- WebSocket now authenticates via `authenticate_websocket`, which raises
  `WebSocketAuthError` after closing the socket; the handler catches it and
  returns.

## Verification

- New tests in `tests/test_task_route_rbac.py`: the session is not in a
  transaction after an allowed call (connection released); introspection
  over the projects.py routes and the updated tasks_logs roles; mcp_stdio:
  org-B key on an org-A project → 403 with the flag off; same-org member key
  passes; shadow would-deny for a viewer key is audited and proceeds;
  legacy admin key passes. rmux: attach denied for a viewer when enforced,
  allowed in shadow; WS closes 4003 for a stranger when enforced.
- `pytest tests/test_task_route_rbac.py tests/test_mcp_stdio_proxy.py tests/rmux`
  and the repo's pre-commit suite; `ruff`, `scripts/ratchet_lint.py` if present.
