---
status: approved
issue: 1650
intent: intent/2026-11-20-1650-tf-project-authz.md
---

# Spec: Project role and token-scope enforcement (`require_project_role`)

## Design

### 1. Core authorization contract (`apps/web-server/server/routes/project_authz.py`)

We mirror AIFactory#2181's shared contract in `server/routes/project_authz.py`:

- **Role hierarchy**: `ROLE_LEVELS = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}` (from `server/routes/organizations.py:42`).
- **Scope ceiling**:
  - Read scopes (`mcp:read`) → `"viewer"`
  - Write scopes (`project:write`, `task:write`, `plan:write`, `mcp:write`) → `"member"`
  - Merge / admin scopes (`task:merge`, `api:full`) → `"admin"` (`api:full` on an org owner allows `"owner"`)
  - OIDC / JWT user session (no `api_key_id` / `scopes`) → no scope ceiling (uses team grant role from `OrgMember`).
- **Effective role**: `min(team_grant_role, scope_ceiling_role)` by `ROLE_LEVELS`.
- **Signature & return**:
  `async def require_project_role(principal: dict[str, Any] | None, project_id: str | None, min_role: str = "viewer", db: AsyncSession | None = None) -> EffectiveProjectAccess`
  - No principal → `401 Unauthorized`.
  - Local mode (`DISABLE_AUTH=True`) → returns `EffectiveProjectAccess(project_id=project_id, org_id="default", role="owner")` (implicit default team).
  - Legacy host-wide shared token (`principal.get("is_legacy_token") is True`, authenticated via `settings.API_TOKEN`): if `settings.LEGACY_SHARED_TOKEN_FULL_ACCESS` (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`, default `True`) is enabled, logs a structured deprecation warning (`authz.legacy_token_bypass`) and grants `"owner"` on existing projects (still raising `404` when `project_id` is unknown on project-targeted routes). When `False`, receives no bypass.
  - Scoped service keys (`acw_` keys in `auth.py`, `mcp_stdio/auth.py`, `mcp_remote/auth.py`): **never** set `is_legacy_token`; their principal carries `scopes`, `org_id`, `user_id`, and `api_key_id`. They must hold a matching `OrgMember` grant (or org-bound service grant on `key.org_id == project["org_id"]`) intersected with `scope_ceiling(scopes)`.
  - **404 vs 403 rule**: If `project_id` is not in the project store, has no `org_id` (outside local default-team mode), belongs to an org different from `principal.get("org_id")`, or the principal has no team grant (< `viewer`), raise `404 Not Found ("Project not found")` so existence is never leaked. If the principal has at least `viewer` visibility on the project but `effective_role < min_role`, raise `403 Forbidden` and emit an out-of-band audit row (`_audit_authz_denial`).

### 2. Project resolution helpers

- **By ID or name**: `resolve_project_id(ref: str) -> str | None` resolves an exact `project_id` or unique `name` match from `load_projects()`.
- **By bare `spec_id` / `task_id`**: `resolve_task_project(task_id: str, visible_project_ids: set[str] | None) -> tuple[str, str]` splits `project_id:spec_id` or scans `workspaces/<pid>/specs/<spec_id>` and `<project_path>/.tfactory/specs/<spec_id>` **restricted to `visible_project_ids`**. Zero matches → `404`; >1 visible matches → `409 Conflict` requiring explicit `project_id`.
- **By filesystem path or repo**: `project_id_for_path(path)` (`server/pty/access.py:56`) and `_projects_for_repo(repo)` (`server/routes/coverage.py:157`) map a path or `owner/repo` back to its registered `project_id` before calling `require_project_role`.

### 3. Complete inventory of project-reaching surfaces

| Surface / Path | How owning project is resolved | Required role |
|---|---|---|
| `GET /api/projects` | Filter `load_projects()` to projects with effective role ≥ `viewer` | `viewer` (filter) |
| `POST /api/projects` | `creator_org(principal, db)` stamps `org_id`; verifies `member` in target org | `member` |
| `POST /api/projects/scan`, `GET /api/files/discover` | Host browse roots; filter discovered paths against unreadable registered projects | `member` |
| `GET /api/projects/{project_id}`, `/{project_id}/version`, `/{project_id}/worktrees`, `/{project_id}/tasks`, `/{project_id}/tasks/{spec_id}/logs`, `/{project_id}/regression`, `/{project_id}/mcp-status` | Path `project_id` | `viewer` |
| `POST /api/projects/{project_id}/tasks/{spec_id}/logs/{watch,unwatch}` | Path `project_id` | `viewer` |
| `PUT /api/projects/{project_id}`, `PATCH /api/projects/{project_id}/settings` | Path `project_id` | `admin` |
| `DELETE /api/projects/{project_id}` | Path `project_id` | `admin` |
| `POST /api/projects/{project_id}/initialize`, `/{project_id}/tasks`, `/{project_id}/tasks/{archive,unarchive}`, `/{project_id}/regression/run` | Path `project_id` | `member` |
| `GET /api/projects/{projectId}/github/{repositories,status,issues,issues/{n},issues/{n}/comments,prs,prs/{n}/review,prs/{n}/new-commits,prs/{n}/logs}` | Path `projectId` (router-level dependency on `github.project_router` & `github_pr.router`) | `viewer` |
| `POST /api/projects/{projectId}/github/{issues/{n}/investigate,import,issues/{n}/close,releases}`, `POST /prs/{n}/{review,post-review,comment,approve,assign,cancel}`, `DELETE /prs/{n}/review` | Path `projectId` | `member` |
| `POST /api/projects/{projectId}/github/prs/{prNumber}/merge` | Path `projectId` | `admin` |
| `GET /api/projects/{projectId}/changelog{,/branches,/tags}`, `POST /changelog/{done-tasks,specs,suggest-version,suggest-version-commits,commits-preview}` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/changelog/{generate,save,images}` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/insights{,/providers,/sessions}` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/insights/{message,stop,create-task,generate-task,sessions,sessions/{id}/switch}`, `PATCH /insights/sessions/{id}{,/model}`, `DELETE /insights{,/sessions/{id}}`, `DELETE /api/projects/{projectId}/files/insights` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/{context,memory/status,memory/search,memory/recent,claude-auth}` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/{context/refresh,claude-setup}` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/env`, `PATCH /api/projects/{projectId}/env` | Path `projectId` (reads/writes `.tfactory/.env` secrets) | `admin` |
| `POST /api/projects/{projectId}/git/{squash,worktree}`, `POST /api/projects/{projectId}/releases{,/preflight}` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/releases/versions` | Path `projectId` | `viewer` |
| `GET /api/projects/{projectId}/auto-fix/{config,queue}` | Path `projectId` | `viewer` |
| `PUT /api/projects/{projectId}/auto-fix/config` | Path `projectId` (writes `botToken`) | `admin` |
| `POST /api/projects/{projectId}/auto-fix/{check-new,{issueNumber}/start}` | Path `projectId` | `member` |
| `GET /api/tasks`, `GET /api/tasks/running` | Filter to `visible_project_ids` (or authorize query `project_id` at `viewer`) | `viewer` (filter) |
| `POST /api/tasks`, `POST /api/tasks/create-and-run` | Body/query `project_id` | `member` |
| `GET /api/tasks/{task_id}`, `/{task_id}/{logs,qa-report,agent-console/sse,plan-html,status,running}`, `/{task_id}/worktree/{merge-preview,status,diff}`, `POST /{task_id}/logs/{watch,unwatch}` | `task_id` (`project_id:spec_id` or bare ingest `spec_id` via `resolve_task_project`) | `viewer` |
| `PUT/PATCH /api/tasks/{task_id}`, `PATCH /{task_id}/status`, `DELETE /{task_id}`, `POST /{task_id}/{clarifications,clarifications/answers,approve-plan,reject-plan,start,stop,recover}`, `POST /{task_id}/worktree/{resolve-conflicts,resolve-uncommitted,resolve-git-merge,abort-merge,discard}` | `task_id` (`project_id:spec_id` or bare `spec_id` resolved across visible projects) | `member` |
| `POST /api/tasks/{task_id}/worktree/{create-pr,merge}` | `task_id` prefix `project_id` | `admin` |
| `POST /api/tasks/worktree/{open-in-ide,open-in-terminal}` | Body `worktreePath` → `project_id_for_path` | `member` |
| `POST /api/specs/ingest` | Body `project_id` (ID or name alias) or `repo`/`git_url` self-register in `creator_org` | `member` |
| `POST /api/specs/{project_id}/{spec_id}/pr` | Path `project_id` (ID or name alias) | `member` |
| `GET /api/tfactory/tasks` | Filter workspace scan to `visible_project_ids` | `viewer` (filter) |
| `GET /api/tfactory/tasks/{spec_id}` + `/{spec_id}/{verdicts.json,ac-fidelity.json,ac-fidelity.md,triage-report.json,triage-report.md,test-plan.json,pr-comment-body.md,catalog,evidence/{t}/{a},screenshots/{a},videos/{a},visual-baselines,visual-baselines/{t}/{s}}` | Optional `?project_id=` or bare `spec_id` via `resolve_task_project` over visible projects | `viewer` |
| `POST /api/tfactory/tasks/{spec_id}/{dismiss,visual-baselines/{t}/{s}/accept}` | Bare `spec_id` (+ optional `?project_id=`) via `resolve_task_project` | `member` |
| `POST /api/tfactory/tasks/{spec_id}/merge` | Bare `spec_id` via `resolve_task_project` (commits to branch) | `admin` |
| `GET /api/files/{project_id}/{list,read,search,diff}` | Path `project_id` | `viewer` |
| `PUT /api/files/{project_id}/write`, `DELETE /api/files/{project_id}/delete` | Path `project_id` | `member` |
| `GET /api/files/{list,read,serve}` | Query `path` / `root` → `project_id_for_path` | `viewer` |
| `GET /api/git/{branches,current-branch,main-branch,status}` | Query `path` → `project_id_for_path` | `viewer` |
| `POST /api/git/init` | Body `path` → `project_id_for_path` | `member` |
| `GET /api/github/detect-repo`, `GET /api/github/fork-info` | Query `path` / `project_path` → `project_id_for_path` | `viewer` |
| `POST /api/github/remote`, `POST /api/github/persist-token`, `GET /api/github/auto-detect?projectId=` | Body `projectPath` / `projectId` | `admin` |
| `GET /api/coverage` | Query `project_id` or `_projects_for_repo(repo)` filtered to `viewer` | `viewer` |
| `POST /api/coverage` | Body `repo` → `_projects_for_repo(repo)` | `member` |
| `GET /api/terminals`, `GET /api/terminals/sessions` | Filter active sessions to `may_use(principal, session)` (`viewer` on `session.project_id`) | `viewer` (filter) |
| `POST /api/terminals`, `DELETE /api/terminals/sessions`, `GET/POST/DELETE /api/terminals/worktrees*`, `GET/DELETE/POST /api/terminals/{terminal_id}*` | `project_id` or `project_id_for_path(cwd/project)` + session owner check | `member` (`viewer` on GET) |
| `POST /api/handback/aifactory-complete` | `X-TFactory-Handback-Token` shared secret + verify `(project_id, spec_id)` belongs to registered project | `member` (webhook) |
| `POST /mcp` (`mcp_copilot.py`) | `COPILOT_MCP_TFACTORY_TOKEN` + verify `task_id` (`project_id:spec_id`) belongs to registered project | `viewer` / `member` (report) |
| `/api/mcp-stdio/*` (16 proxy routes in `mcp_stdio/router.py`) | `require_acw_access(scope, min_role)` / `require_acw_principal(scope)` delegating to `require_project_role` | `viewer` (`mcp:read`), `member` (`*:write`), `admin` (`task:merge`) |
| `/api/mcp-remote/*` (12 tools in `mcp_remote/tools.py`) | `_authorize_call` & `_list_projects` via `require_project_role` with key's `scopes` + `org_id` | `viewer` / `member` / `admin` (`merge_pr`) |
| `task_control.py` (7 tools: `project_list`, `project_create`, `task_list`, `task_status`, `report_get`, `task_create_and_run`, `task_create_from_spec`, `task_rerun`) | Cluster mode routes through `/api/mcp-stdio/*`; local mode filters/authorizes via caller principal (`TFACTORY_PRINCIPAL_ORG_ID` / default local team) and scopes bare `task_id` lookups | `viewer` / `member` |
| WebSockets: `/ws/tasks/{task_id}/{logs,progress}`, `/ws/{logs,progress}`, `/ws/terminal/{terminal_id}`, `/ws/events`, `/api/tfactory/tasks/{spec_id}/logs/stream`, `/api/tasks/{spec_id}/agent-console/ws`, `POST /api/tasks/{spec_id}/agent-console/{attach,detach}` | `authenticate_websocket` (including `acw_` scope ceiling and `LEGACY_SHARED_TOKEN_FULL_ACCESS`) → `require_project_role` (`viewer` for streams, `member` for console attach/detach & terminal PTY) | `viewer` / `member` |

## Alternatives rejected

- **Keep `RBAC_ENFORCE_TASK_ROUTES` shadow mode**: Leaves cross-team reads and unguarded routes open before Phase 3's shared registry lands.
- **Return 403 for non-member project IDs**: Leaks whether a project UUID or name exists in another team.
- **Allow `acw_` service keys with `user_id=None` to use the legacy bypass**: Violates `token scopes ∩ team grants`; only `APP_API_TOKEN` under `APP_LEGACY_SHARED_TOKEN_FULL_ACCESS=true` gets the transitional bypass.

## Risks

- **Breaking `parr-run` or AIFactory handoffs**: Mitigated by defaulting `APP_LEGACY_SHARED_TOKEN_FULL_ACCESS=true` in Phase 2 while logging deprecation warnings, and supporting name-or-id project resolution in `require_project_role`.
- **Bare `spec_id` collisions across projects**: Scanning only projects visible to the principal prevents cross-tenant hijacking and returns `404` when the spec lives only in another team's project.

## Verification

- Parametrized route-table compliance test (`tests/test_project_authz_route_table.py`) inspecting `create_app().routes` (REST + WS) and MCP tools (`mcp_stdio`, `mcp_remote`, `task_control`): fails if any project-scoped endpoint lacks `require_project_role` or an allowlisted public/non-project exemption.
- Behavioral authz matrix tests: unknown project → `404`, other team's project → `404`, visible project with role < `min_role` → `403`, token scope ceiling (`mcp:read` key on `owner` user → `viewer` effective role → `403` on mutation), scoped service key denied on another team's project (`404`), `LEGACY_SHARED_TOKEN_FULL_ACCESS=false` revokes `APP_API_TOKEN` bypass, single-user `DISABLE_AUTH=true` succeeds via default team.
