---
status: draft
issue: 1650
author: olafkfreund
---

# Intent: Enforce project-level role and scope authorization across all TFactory surfaces

## Problem

TFactory's project and task surfaces do not uniformly enforce team ownership or token scope ceilings (Factory#3857 phase 2, priority P3 in Factory#3864):

- `GET /api/projects` (`apps/web-server/server/routes/projects.py:361-370`), `GET /api/tasks` (`routes/tasks.py:1171-1205`), and `GET /api/tfactory/tasks` (`routes/tfactory_tasks.py:241-278`) return every registered project or workspace without filtering by the caller's team grants.
- Existing checks in `routes/project_authz.py:15-16,55-93` run in shadow mode by default (`RBAC_ENFORCE_TASK_ROUTES=False`), return 403 instead of 404 when a project is invisible to the caller (leaking project existence across teams), ignore API-key scope ceilings on REST/WS routes, and grant an unconditional bypass to any principal marked `is_service`.
- Indirect paths reach project data without authorization: `POST /api/specs/ingest` and `POST /api/specs/{project_id}/{spec_id}/pr` resolve projects by ID, display name, or `repo`/`git_url` (`routes/specs.py:371-591`); `/api/tfactory/tasks/{spec_id}*` (`routes/tfactory_tasks.py:93-110,280-1071`), `DELETE /api/tasks/{task_id}` (`routes/tasks.py:1566-1635`), `GET /api/tasks/{task_id}/running` (`routes/execution.py:221-231`), and MCP tools in `apps/backend/agents/tools_pkg/tools/task_control.py:213-253,1029-1718` scan workspaces by bare `spec_id` or mutate `projects.json` from caller-supplied IDs; path-based routes in `files.py`, `git.py`, `github.py`, `coverage.py`, and `worktree_tools.py` operate on raw filesystem paths or repo names without checking the owning project.

Phase 2 must ship before Phase 3's shared project registry widens visibility across portals.

## Proposed outcome

- A single authorization check `require_project_role(principal, project_id, min_role)` enforces `effective_role = min(team_grant_role, token_scope_ceiling)` across `ROLE_LEVELS` (`viewer < member < admin < owner`).
- Unknown projects and projects where the caller lacks even `viewer` access both raise HTTP `404` (revealing nothing); visible projects where the caller's effective role is below `min_role` raise HTTP `403`.
- All list endpoints return only projects and tasks where the caller holds at least `viewer`.
- Every direct and indirect route, WebSocket, and MCP tool resolves the owning project first, authorizes via `require_project_role`, and only then acts.
- The legacy host-wide `APP_API_TOKEN` retains full access only while `LEGACY_SHARED_TOKEN_FULL_ACCESS` (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`, default `True` in phase 2) is enabled and logs a deprecation warning on each use; scoped service keys (`acw_`) never bypass team grants.
- Single-user/local mode (`DISABLE_AUTH=True`) continues working seamlessly via the implicit `"default"` team with the local user as `owner`.
- A generated parametrized authz test walks the FastAPI route table (REST + WebSocket) and MCP tool registries and fails if any project-scoped entry lacks enforcement.

## Affected users and systems

- TFactory web-server (`apps/web-server/server/`): REST routes, WebSockets, `mcp_stdio`, `mcp_remote`, `rmux`, and `project_authz.py`.
- TFactory backend MCP tools (`apps/backend/agents/tools_pkg/tools/task_control.py`).
- Cross-portal callers (AIFactory, PFactory, CFactory, `parr-run`, Copilot MCP, handback webhook).

## Constraints

- Must not break running cluster traffic or `parr-run` during Phase 2 rollout (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS` defaults to `True`).
- Must not touch byte-exact vendored files (`scripts/ratchet_helpers.py`, `apps/backend/agents/verification_gate.py`, `apps/backend/tools/runners/{nix_provisioner,artifact_store,language_descriptors}.py`, `apps/backend/runners/github/**`).
- Must pass the diff-scoped ruff and `mypy --strict` lint ratchet (`scripts/ratchet_lint.py`) with zero net-new violations and keep `ProjectStore` (`JsonProjectStore` / `DbProjectStore`) compatibility intact.

## Open questions

1. Should `GET /api/badges/{project_id}/{spec_id}/test-acceptance.svg` remain unauthenticated in `PUBLIC_PREFIXES` (returning only aggregate pass/fail SVG for README embeds), or require a signed badge token in a later phase?
2. For `POST /api/specs/ingest` when `project_id` is not yet registered and `git_url` triggers self-registration, should `require_project_role` require `member` in the caller's resolved `creator_org` before cloning? (Proposed: yes.)

## Decisions (coordinator, 2026-11-20, under the owner's standing delegation)

1. `GET /api/badges/.../test-acceptance.svg` stays public in this phase, because it returns only an aggregate pass/fail. A signed badge token is a later follow-up.
2. `POST /api/specs/ingest` self-registration from `git_url` requires `member` in the caller's resolved creator org before cloning.
