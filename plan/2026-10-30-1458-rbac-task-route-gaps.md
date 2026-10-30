---
status: approved
issue: 1458
spec: spec/2026-10-30-1458-rbac-task-route-gaps.md
coders:
  1: agy   # the lead's brief: the user asked for agy as this lane's implementer
  2: agy
  3: agy
  4: agy
---

# Plan: close the task-route RBAC gaps before S6

All paths are under `apps/web-server/server/` unless they start with `tests/`.
`RBAC_ENFORCE_TASK_ROUTES` stays default off (shadow: log + audit would-deny,
never change a response, fail open on lookup errors).

Approved decisions (from the spec):

- `ProjectAccessChecker.check(user, project_id, db) -> dict[str, Any]` holds
  the shadow/enforce logic; `_authorize` delegates to it. The call to
  `authorize_project_for_user` sits in its own `try/finally` whose
  `finally` is `with contextlib.suppress(Exception): await db.rollback()`,
  so the pooled connection is released on allow, deny and error, before the
  audit write or the handler. Remove the now-redundant deny-path rollback.
  Keep `enforce = _enforcing()` before the `try`; return the principal on
  every path.
- Roles: task create/archive/unarchive, console attach/detach, all mcp_stdio
  task proxies = member. Log watch/unwatch (both route families) and the
  console WebSocket = viewer.
- mcp_stdio principal: legacy key → `{"id": "default", "is_service": True}`;
  `acw_` key → `{"id": key.user_id, "role": "user", "api_key_id": key.key_id,
  "org_id": key.org_id}`. The cross-org check (key `org_id` and project
  `org_id` both set and different) is always enforced: audit
  `authz.task.denied` via `_audit_authz_denial`, raise 403. The rest follows
  the flag through `check`.

## Steps

1. `routes/project_authz.py:200-250`: extract `check` and add the
   `try/finally` rollback as above. Tests in `tests/test_task_route_rbac.py`:
   (a) after an allowed member call the request session was rolled back
   (wrap the fixture's `_db` session so the test can see `rollback` was
   awaited, or assert `not session.in_transaction()` after the call);
   (b) the same on the enforced deny path.
   → `pytest tests/test_task_route_rbac.py -q`
   Traps: no variable first assigned inside `try` and read in `except`
   (CodeQL); one return type; do not take a second session for the
   membership query.

2. `routes/projects.py:1091,1191,1202,1242,1306`: add
   `dependencies=[Depends(ProjectAccessChecker(<role>))]` (member for create,
   archive, unarchive; viewer for logs watch/unwatch). Import `Depends` from
   fastapi and `ProjectAccessChecker` from `.project_authz` at module top
   (`project_authz` imports `load_projects` lazily, so no cycle).
   `routes/tasks_logs.py:175-188`: watch/unwatch → `require_task_access("viewer")`.
   Tests: extend the introspection test (`EXPECTED` carries the role per
   route; logs watch/unwatch now viewer) and add one for the 5 projects.py
   routes checking `ProjectAccessChecker` with the right role.
   → `pytest tests/test_task_route_rbac.py -q`
   Traps: the existing `assert len(EXPECTED) == 20` stays 20 for task routes.

3. `rmux/bridge.py:144-260`: attach and detach call
   `await ProjectAccessChecker("member").check(getattr(request.state, "user", None),
   spec_id.split(":", 1)[0], db)` before `_resolve_state_or_404`. The WebSocket
   (`:254`): replace `verify_websocket_token` with `authenticate_websocket`
   (catch `WebSocketAuthError` and return; `None` → `{"id": "default",
   "is_service": True}`); then `async with async_session_factory() as db:`
   run `ProjectAccessChecker("viewer").check(...)`; on `HTTPException` accept
   and close with code 4003, return. Tests in `tests/rmux/test_bridge.py`:
   enforced viewer attach → 403; shadow viewer attach → 200; existing tests
   still pass (their db override yields `None`; `check` must tolerate that:
   the rollback is suppressed, and the membership query only runs when a
   project has `org_id`).
   → `pytest tests/rmux -q`
   Traps: `get_db` is overridden to yield `None` in the bridge tests; patch
   `server.routes.projects.load_projects` (not a reloaded module) in tests.

4. `mcp_stdio/router.py:199-332`: add `_authorize_key(key, project_id, db)`
   implementing the principal mapping, the always-enforced cross-org check
   and `ProjectAccessChecker("member").check`. Each task proxy
   (create-and-run with its `project_id` query; start, stop, recover,
   approve-plan, create-pr, merge with `task_id.split(":", 1)[0]`) gains
   `db: AsyncSession = Depends(get_db)` and awaits `_authorize_key` before
   delegating. Tests in `tests/test_task_route_rbac.py` (or a new
   `tests/test_mcp_stdio_authz.py`): org-B key on an org-A project → 403
   with the flag off, and no delegated handler call; same-org member key →
   handler runs; viewer key in shadow → audited would-deny, handler runs;
   legacy admin key → handler runs.
   → `pytest tests/test_mcp_stdio_proxy.py tests/test_task_route_rbac.py -q`
   Traps: do not hold a connection across the delegated call (`check`'s
   `finally` covers it); keep audit-on-success behaviour unchanged.

## Tests

`pytest tests/test_task_route_rbac.py tests/test_mcp_stdio_proxy.py tests/rmux -q`,
`ruff check` and `ruff format --check` on touched files,
`python scripts/ratchet_lint.py --base origin/dev`; pre-commit runs the suite.

## Rollback

Revert the PR. The flag is off, so only the cross-org mcp_stdio refusal
changes behaviour, and it cannot fire until projects carry `org_id`.

## Deviations (lead's review of #1465)

- Step 3: the rmux registry is keyed by bare spec id and `_resolve_state_or_404`
  falls back to the suffix, so `pA:<pB-spec>` was authorized against pA and
  reached pB's session. `SessionState` now records `project_id`
  (`create_for_task` / `create_if_enabled` take it; `agent_service` passes
  the task id's project prefix) and `_resolve_state_or_404` returns 404 when
  the requested prefix differs. This applies whatever the flag says: it is
  resource resolution, not role policy. Regression test:
  `tests/rmux/test_bridge.py::TestCrossProjectSpec`.
- Step 4: `_authorize_key`'s `load_projects()` failure fails open in shadow
  mode (raises when enforcing), like `check`.
- Follow-up issue (not in this PR): `GET /{task_id}/agent-console/sse`
  (`routes/tasks_views.py`) has no project check, and the console audit rows
  read `request.state.user_id/org_id`, which the middleware never sets.
