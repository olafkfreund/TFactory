---
status: approved
issue: 1650
spec: spec/2026-11-20-1650-tf-project-authz.md
coders:
  1: codex   # core authz rule, scope-ceiling intersection, legacy token flag, and principal propagation
  2: agy     # mechanical sweep of /api/projects/*, sub-routers, and path-resolved file/git/github/coverage routes
  3: agy     # mechanical sweep of /api/tasks/*, /api/tfactory/tasks/*, /api/specs/*, and bare spec_id resolution
  4: codex   # MCP (mcp_stdio, mcp_remote, task_control.py) and WebSocket authz + terminal access wiring
  5: codex   # generated route-table + MCP authz compliance and matrix test suite
reviewers:
  - codex
  - agy
---

# Plan: Enforce `require_project_role`, filtered lists, and indirect-path checks

Self-contained summary of approved spec decisions:
- `require_project_role(principal, project_id, min_role)` computes `effective_role = min(team_grant_role, token_scope_ceiling)` over `ROLE_LEVELS` (`viewer < member < admin < owner`).
- Unknown project OR caller lacks `viewer` visibility (no membership, unowned project outside local mode, or key bound to another org) → `404 Not Found`. Caller has ≥ `viewer` but `effective_role < min_role` → `403 Forbidden` (plus background audit event).
- Scope ceiling: `mcp:read` → `viewer`; `project:write` / `task:write` / `plan:write` / `mcp:write` → `member`; `task:merge` / `api:full` → `admin` (`owner` when key owner is org `owner`). OIDC/JWT sessions have no scope cap.
- `LEGACY_SHARED_TOKEN_FULL_ACCESS` (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`, default `True`) gates the legacy `APP_API_TOKEN` bypass and logs a deprecation warning on each use. Scoped `acw_` keys never bypass. Single-user/local mode (`DISABLE_AUTH=True`) uses the default team (`"default"`, role `"owner"`).
- All list routes filter to projects with ≥ `viewer`. Every indirect path (spec ingest, bare `spec_id`/`task_id` lookups, raw `path`/`repo` params, WebSockets, MCP tools) resolves the owning project first and authorizes via `require_project_role`.

## Steps

1. `apps/web-server/server/config.py:30-45,142-148`, `apps/web-server/server/auth.py:180-302,369-442`, `apps/web-server/server/routes/project_authz.py:1-317`:
   - Add `LEGACY_SHARED_TOKEN_FULL_ACCESS: bool = True` to `Settings` (env `APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`).
   - In `auth.py`, tag legacy `API_TOKEN` principals with `"is_legacy_token": True` (REST and WS), and attach `"scopes": sorted(key.scopes)` + `"scoped_service": key.user_id is None` on `acw_` principals (never `is_legacy_token`).
   - In `project_authz.py`, implement `SCOPE_CEILING`, `EffectiveProjectAccess`, `require_project_role`, `visible_project_ids`, `resolve_project_id`, and `resolve_task_project`; update `ProjectAccessChecker` / `TaskAccessChecker` to enforce unconditionally (404 on unknown/invisible, 403 on insufficient role) and log deprecation when `is_legacy_token` uses `LEGACY_SHARED_TOKEN_FULL_ACCESS`.
   → verify by `/mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest tests/test_mcp_stdio_authz.py tests/test_mcp_remote_org_authz.py -v`
   Traps: release `db` connection via `await db.rollback()` before `_audit_authz_denial` so Postgres pooled connections never deadlock; preserve `ProjectStore` (`JsonProjectStore` / `DbProjectStore`) seam; pass `scripts/ratchet_lint.py` (`ruff` + `mypy --strict`).

2. `apps/web-server/server/routes/projects.py:37-58,361-1245`, `routes/files.py:233-978`, `routes/git.py:76-194,960-1460`, `routes/github.py:473-556,831-1483`, `routes/github_pr.py:32-473`, `routes/github_auth.py:180-221,427-436`, `routes/changelog.py:24-1149`, `routes/context.py:106-902`, `routes/auto_fix.py:35-131`, `routes/regression.py:36-72`, `routes/mcp.py:29-160`, `routes/coverage.py:190-283`:
   - Filter `GET /api/projects` via `visible_project_ids(principal, db, "viewer")`.
   - Attach `ProjectAccessChecker` (`viewer` for reads, `member` for writes, `admin` for settings/env/delete/PR-merge/auto-fix-config) to `/api/projects/{project_id}*` and all sub-routers (`github`, `github_pr`, `changelog`, `insights`, `context`, `git`, `releases`, `auto_fix`, `regression`, `mcp`).
   - On path/repo endpoints (`files.py` `/list`, `/read`, `/serve`; `git.py` `/branches`, `/current-branch`, `/main-branch`, `/status`, `/init`; `github.py` `/detect-repo`, `/fork-info`, `/remote`; `github_auth.py` `/auto-detect`, `/persist-token`; `coverage.py` `GET/POST /api/coverage`), resolve `project_id` via `project_id_for_path` or `_projects_for_repo` and call `require_project_role`.
   → verify by `/mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest apps/web-server/tests/test_projects_store_wiring.py apps/web-server/tests/test_project_env_allowlist.py -v`
   Traps: sub-routers use `{projectId}` while `projects.py` uses `{project_id}` — `ProjectAccessChecker` must accept either path param (`request.path_params.get("project_id") or request.path_params.get("projectId")`); do not touch vendored `apps/backend/runners/github/**`.

3. `apps/web-server/server/routes/tasks.py:1171-1635`, `routes/tasks_logs.py:30-173`, `routes/tasks_views.py:29-78,186-243`, `routes/tasks_worktree.py:60-2403`, `routes/worktree_tools.py:249-328`, `routes/execution.py:91-232,825-890`, `routes/specs.py:371-591`, `routes/tfactory_tasks.py:93-110,241-1071`, `routes/handback.py:138-223`:
   - Filter `GET /api/tasks`, `GET /api/tasks/running`, and `GET /api/tfactory/tasks` to `visible_project_ids`.
   - Add `require_task_access("viewer")` to `GET /api/tasks/{task_id}`, `/{task_id}/logs`, `/{task_id}/qa-report`, `/{task_id}/plan-html`, `/{task_id}/running`; upgrade `create-pr` and `merge` in `tasks_worktree.py` to `require_task_access("admin")`.
   - Authorize `POST /api/tasks` and `POST /api/tasks/create-and-run` against `project_id` (`member`), `worktree_tools.py` `open-in-ide`/`open-in-terminal` via `project_id_for_path(request.worktreePath)` (`member`), and `specs.py` `POST /api/specs/ingest` + `POST /api/specs/{project_id}/{spec_id}/pr` via `require_project_role` (`member`, or `creator_org` check on self-register).
   - Replace bare `_find_spec_dir` in `tfactory_tasks.py` and bare `spec_id` deletion in `tasks.py:1571-1635` with `resolve_task_project` scoped to the caller's `visible_project_ids` (`viewer` for reads/WS stream, `member` for `dismiss`/`accept`, `admin` for `merge`).
   → verify by `/mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest tests/test_tfactory_routes_tasks.py -v`
   Traps: preserve `InputRejectedError` / `client_error` sanitization in `specs.py` and `tfactory_tasks.py`; keep CodeQL `safe_component` / `safe_spec_dir` barriers intact.

4. `apps/web-server/server/mcp_stdio/auth.py:59-143`, `server/mcp_stdio/router.py:123-596`, `server/mcp_remote/auth.py:125-216`, `server/mcp_remote/tools.py:85-159,355-498`, `server/routes/mcp_copilot.py:103-115,445-518`, `server/pty/access.py:22-38`, `server/routes/terminal.py:155-350`, `server/websockets/task_access.py:24-160`, `server/websockets/events.py:124-158,227-323`, `apps/backend/agents/tools_pkg/tools/task_control.py:213-253,1029-1718`:
   - Refactor `mcp_stdio/auth.py` and `router.py` to mirror AIFactory `mcp_stdio/auth.py:183` (`acw_principal`, `require_acw_principal`, `require_acw_access`), routing all 16 proxy endpoints through `require_project_role` with `admin` required for `TASK_MERGE_SCOPE` routes.
   - Update `mcp_remote/tools.py` to pass `scopes` on `_principal(key)` into `require_project_role` (`admin` for `tfactory.merge_pr`, `member` for writes, `viewer` for reads) and filter `_list_projects` via `visible_project_ids`.
   - Update `pty/access.py`, `routes/terminal.py`, `websockets/task_access.py`, and `websockets/events.py` to use `require_project_role` (removing the old `RBAC_ENFORCE_TASK_ROUTES` shadow bypass).
   - In `task_control.py`, scope `_find_tasks` / `_locate_local` / `task_list` / `project_list` / `project_create` / `task_create_*` to the caller's authorized org/projects (and stamp `org_id` on `project_create`).
   → verify by `/mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest tests/test_mcp_stdio_authz.py tests/test_mcp_remote_org_authz.py tests/test_tfactory_mcp_tools.py -v`
   Traps: `from __future__ import annotations` in `mcp_stdio/auth.py` requires `Depends(require_acw_scope(scope))` as a default value, not inside `Annotated`; do not touch vendored files in `apps/backend/tools/runners/`.

5. `tests/test_project_authz_route_table.py` (new):
   - Walk `create_app().routes` (REST + WebSocket) and MCP tool registries (`mcp_stdio`, `mcp_remote`, `task_control`) and assert every project/task/file/git/terminal route outside the explicit non-project allowlist invokes `require_project_role` / `ProjectAccessChecker` / `TaskAccessChecker`.
   - Parametrize the behavioral contract across REST, WS, and MCP: unknown project → `404`, non-member project → `404`, insufficient role → `403`, token scope ∩ team grant ceiling (`mcp:read` + `owner` → `viewer`), cross-team service key → `404`, `LEGACY_SHARED_TOKEN_FULL_ACCESS=False` denies `APP_API_TOKEN`, and `DISABLE_AUTH=True` succeeds via the default team.
   → verify by `/mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest tests/test_project_authz_route_table.py -v && python scripts/ratchet_lint.py --base dev --package apps/backend --package apps/web-server`
   Traps: new test file has a base lint count of 0 in `scripts/ratchet_lint.py`, so it must pass `ruff` (`standards/ruff.toml`) and `mypy --strict` (`standards/mypy.ini`) with zero warnings.

## Tests

```bash
/mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest \
  tests/test_project_authz_route_table.py \
  tests/test_mcp_stdio_authz.py \
  tests/test_mcp_remote_org_authz.py \
  tests/test_tfactory_mcp_tools.py \
  tests/test_tfactory_routes_tasks.py \
  apps/web-server/tests/test_projects_store_wiring.py -v
python scripts/ratchet_lint.py --base dev --package apps/backend --package apps/web-server
```

## Rollback

Revert the PR or set `APP_LEGACY_SHARED_TOKEN_FULL_ACCESS=true` (already the Phase 2 default) if a legacy M2M caller relies on `APP_API_TOKEN`. No destructive schema migration is required.
