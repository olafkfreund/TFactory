---
status: approved
issue: 1450
intent: intent/2026-10-27-1450-task-route-rbac.md
---

# Spec: task-route role checks and API-key scope caps

The design is the approved hub plan's slice S1
(`olafkfreund/Factory:plan/2026-10-27-312-fleet-rbac-role-model.md`); this
records it for this repo.

## Design

1. **Mint cap** (`routes/api_keys.py::create_api_key`), as PFactory #945: a
   principal with `api_key_id` gets 403; a caller whose `OrgMember` role is
   below `member` may request only `mcp:read` (403 otherwise). Uses
   `ROLE_LEVELS`/`_role_level` from `routes/organizations.py`.
2. **Auth cap** (`mcp_remote/auth.py`): `cap_by_owner_role` as in PFactory
   #945, called once inside `_lookup_by_digest`. The REST `acw_` middleware
   path (`auth.py`) goes through `authenticate()` → `_lookup_by_digest`, so
   the `api:full` check sees capped scopes. No second call site.
3. **`routes/project_authz.py`** (new): port of AIFactory's
   `check_project_access`, `authorize_project_for_user` and
   `require_task_access(minimum_role)` (task id `{project_id}:{spec}`).
   `is_service_principal(user)` is `bool(user.get("is_service"))` only
   (post-AIFactory #1911). Projects come from `routes/projects.load_projects`.
4. **Wiring**: `Depends(require_task_access("member"))` on every
   `POST/PUT/PATCH/DELETE` route under `/api/tasks/{task_id}`.
5. **Flag** `RBAC_ENFORCE_TASK_ROUTES` (`config.py`, default `False`). Off:
   every `HTTPException` from the decision is logged and audited as a
   would-deny (AIFactory `_audit_authz_denial` shape) and swallowed; any other
   lookup error fails open with a warning. On: deny as AIFactory does.

## Alternatives rejected

- Enforce now: every TF project lacks `org_id`, so it locks out every human.
- Router-level `dependencies=`: the task routers also carry read routes.
- A second cap call in the REST middleware: `_lookup_by_digest` covers both.

## Risks

- Key caps take effect on merge, unflagged. Mitigation: recount prod
  `api_keys` (read-only, totals) before merge; list owners/scopes if any.
- Shadow mode must not change responses: tested for unknown project and a
  raising lookup.

## Verification

`tests/test_api_key_mint_scope_cap.py`, `tests/test_api_key_rest_role_cap.py`,
`tests/test_mcp_key_lookup.py`, `tests/test_task_route_rbac.py` (cases in
the plan), plus the existing suite green with the flag off.
