---
status: approved
issue: 1450
spec: spec/2026-10-27-1450-task-route-rbac.md
---

# Plan: task-route role checks and API-key scope caps

Steps follow hub plan S1 (`olafkfreund/Factory:plan/2026-10-27-312-fleet-rbac-role-model.md`),
numbered the same. Paths are under `apps/web-server/server/` unless rooted.
Run Python as `apps/backend/.venv/bin/python -m pytest <test> -q` from the repo root.

Decisions (from the spec):
- Mint cap and auth cap copied from PFactory #945 (merge `87ee1ed`).
- `cap_by_owner_role` is called once, inside `_lookup_by_digest`.
- `project_authz.py` ported from AIFactory `origin/dev`; `is_service_principal`
  trusts only `is_service`; projects from `routes/projects.load_projects`.
- `require_task_access("member")` on every task-keyed write route; flag
  `RBAC_ENFORCE_TASK_ROUTES` default off = shadow mode (never changes a response).

## Steps

1. `routes/api_keys.py::create_api_key`: `acw_` principal (`api_key_id` on
   `request.state.user`) → 403; role below `member` in the key's org → only
   `mcp:read` (403 otherwise). Use `ROLE_LEVELS`/`_role_level` from
   `routes/organizations.py`. Copy PFactory's `tests/test_api_key_mint_scope_cap.py`.
   → verify: `tests/test_api_key_mint_scope_cap.py`.
2. `mcp_remote/auth.py`: add `cap_by_owner_role` (PFactory #945) and call it
   once in `_lookup_by_digest`. Store `expires_at`/`last_used_at` as naive UTC
   as PFactory #945 did if TF writes them. Copy and adapt PFactory's
   `tests/test_api_key_rest_role_cap.py` (through TF's `TokenAuthMiddleware`
   `acw_` branch) and `tests/test_mcp_key_lookup.py`; port
   `tests/postgres/test_api_key_expiry_pg.py` if it applies.
   → verify: `tests/test_api_key_rest_role_cap.py tests/test_mcp_key_lookup.py`
   and the existing `tests/test_api_key*.py`.
3. New `routes/project_authz.py`: port `check_project_access`,
   `authorize_project_for_user`, `_audit_authz_denial`, `ProjectAccessChecker`,
   `TaskAccessChecker`, `require_task_access` from AIFactory. Only what step 4
   needs; no `accessible_org_ids`/`resolve_owner_org_id`.
4. Wire `Depends(require_task_access("member"))` on these 20 routes (from
   `git grep -n '@router\.\(post\|put\|patch\|delete\)("/{task_id}' -- apps/web-server/server/routes`):
   execution.py start, stop, recover; tasks.py PATCH status, PUT, PATCH, DELETE;
   tasks_clarifications.py clarifications, clarifications/answers; tasks_logs.py
   logs/watch, logs/unwatch; tasks_plan.py approve-plan, reject-plan;
   tasks_worktree.py resolve-conflicts, resolve-uncommitted, resolve-git-merge,
   abort-merge, create-pr, merge, discard.
5. `config.py`: `RBAC_ENFORCE_TASK_ROUTES: bool = False`. In the checker: flag
   off → catch every `HTTPException` from the decision (403s, missing-project
   404, no-`org_id` 403), log + audit as would-deny, return; any other
   exception → warning log, return (fail open). Flag on → raise.
   New `tests/test_task_route_rbac.py`: viewer 403 on start with flag on, 200
   off; member passes; `is_service` passes; key bound to another org 403; JWT
   `role: admin` gets no bypass; flag off + unknown project → same response as
   without the dependency; flag off + lookup raises → request succeeds; every
   route in step 4 carries the dependency (fails if removed).
   → verify: `tests/test_task_route_rbac.py`, ruff on touched files,
   `scripts/ratchet_lint.py` if present, and the task/execution test files.

Traps: never `git add -A` (the `.venv` symlink is not ignored). No gitops,
no deploy. Before merge: recount prod tfactory `api_keys` rows read-only,
totals only; list owner roles/scopes in the PR if any exist.

## Out of scope (recorded in the PR)

Project- or spec-keyed writes without a `task_id` (`POST /api/tasks`,
`regression/run`, `tfactory_tasks` `/{spec_id}/...`): later slices.

## Tests

As above; with the flag off the full suite is unchanged.

## Rollback

Revert the PR. With the flag on (S6), set it `false` in gitops first.

## Deviations
