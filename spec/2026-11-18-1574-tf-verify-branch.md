---
status: approved
issue: 1574
intent: intent/2026-11-18-1574-tf-verify-branch.md
---

# Spec: verify a cluster-built branch over MCP (#1574, #1583)

## Design

Two halves: a cluster side that can clone and verify a named repo and ref, and an MCP side that talks to it.

### Cluster side (web-server and backend)

1. `create_spec_ingest_workspace` (`task_control.py`) takes `pr_number` and writes it to `source.json`. After the worktree is chosen, a source tree with no entries other than `.git` is a checkout failure: `status=failed`, `phase=source_tree_empty`, `verify=not_run`, `failure_kind=environment`, planner not scheduled. This reuses the existing `checkout_failed_reason` path that already skips the planner for an unresolvable ref (`source_checkout_failed`).
2. `POST /api/specs/ingest` (`routes/specs.py`) gains `pr_number`. For a GitHub `repo` (`owner/name` or an `https://github.com/owner/name` URL) the clone URL is `https://github.com/<owner>/<name>.git`. A non-GitHub `repo` keeps today's behaviour (recorded as provider only, no derivation).
   - If `git_url` is also sent, its owner/name must equal `repo`, else 400 (no cloning one repo while reporting another). The registered project's `clonedFrom`, when present, must match too, else 409 `repo does not match project`.
   - Unknown project plus a clone URL: self-register (existing path).
   - Known project plus GitHub `repo`: the checkout root is `clone_or_update(repo_url)` with no branch (same URL, same dir, idempotent), never the registered project's `path`. The ref is then fetched by the existing per-spec worktree code, so tags and commits work.
   - Credentials: `clone_or_update` hides its askpass helper after the clone, but the per-spec `git fetch` runs later. `create_spec_ingest_workspace` gets an optional `git_env` that `_add_spec_worktree` and `_default_branch` merge into their hermetic git env. The route builds it with the existing `_git_askpass_env("x-access-token", token)` around the call, from `GITHUB_TOKEN`/`GH_TOKEN`. The token is never in argv, config or logs.
   - A clone failure (`GitOperationError`) raises before a task exists: the route answers 502 with a fixed message (no git stderr). This is still early and before any LLM call, but it creates no task record. An unresolvable ref after a good clone is the `source_checkout_failed` task.
   - The response adds `tree` (`cluster_clone` when `repo`/`git_url` selected the root, else `local`), `repo_slug`, `source_sha` and `workspace_project_id` (the resolved id the workspace lives under).
3. New routes on `mcp_stdio/router.py` (the proxy `http_client.request` already rewrites `/api/*` to). Each is scope-gated and org-authorised like its neighbours, and writes audit:
   - Authorisation: the existing `_authorized_key` reads only a `task_id` path param or a `project_id` query param, so the new handlers resolve the project (id or name) to its canonical id first, then call `_authorize_key(key, resolved_id, db, role)` themselves, and only then touch the filesystem. Ids pass `safe_component`.
   - `POST /api/mcp-stdio/specs/ingest` (`task:write`): delegates to `ingest_spec`. A known project goes through `_authorize_key`; an org-scoped key naming an unknown project gets 404 (fail closed, no org-less self-registration); a service/legacy key may self-register. Audited as `mcp.task.create_and_run`.
   - `GET /api/mcp-stdio/spec-tasks?project_id=&status=` (`mcp:read`): rows for the tasks in the workspace, foreign-org projects filtered out. `project_id` accepts an id or a name.
   - `GET /api/mcp-stdio/spec-tasks/{project_id}/{spec_id}?report=md|json` (`mcp:read`, viewer): `status.json` content with derived lane progress, `source_sha`, `repo_slug`, `source_branch`, `pr_number`, and the report body when requested. The lookup is by project id plus spec id, which is the #1583 fix on this path.

### MCP side (`task_control.py`)

- Cluster mode is on when `TFACTORY_API_URL` is set. Off by default, so existing local use is untouched. Requests go through the existing `http_client.request`, which resolves the key and path rewrite. Ingest passes `timeout=660` (a clone can take minutes; the 30 s default would leave the caller unsure). No automatic retry: a repeated spec id answers 409, which tells the caller the first call landed.
- `task_create_from_spec` gains `repo`, `branch` (alias of `ref`; both is an error) and `pr_number`. `task_create_and_run` gains `repo`, `pr_number` and `spec_text`. In cluster mode `create_and_run` sends `branch` as `source_branch` and requires `spec_text` because the cluster TFactory cannot read the AIFactory pod's spec dir; without it the error says so.
- `repo` without `TFACTORY_API_URL` is an error that names the variable, not a silent local fallback.
- `project_list`, `task_list`, `task_status`, `report_get` use the cluster routes in cluster mode. `project_create` stays local (out of scope).
- `task_status`, `report_get` and `task_rerun` accept `project_id`. Locally, `_find_task` checks only that project when given; with no `project_id` and the spec id in more than one project it returns an ambiguity error listing the project ids, instead of the first match. In cluster mode `task_rerun` returns a clear not-available error (no proxy route; out of scope).
- `portal_url` is `TFACTORY_PORTAL_URL`, else `TFACTORY_API_URL`, else the current localhost value, plus `/tasks/{id}`.
- Responses carry `tree` (`local` or `cluster_clone`).

## Alternatives rejected

- Mirror the AIFactory spec dir into the cluster TFactory: needs cross-pod storage and a new credential. `spec_text` is the smaller path.
- Make the MCP server speak the remote HTTP MCP surface (`mcp_remote`): it has no ingest tool and a different auth model; the stdio proxy already exists and is where ingest and reports belong.
- Default cluster mode on: breaks the local-path users. An env switch is explicit.
- Trust the registered project's path when `repo` is given: could verify unrelated code.
- Add `pr_number` only via the existing `POST /api/specs/{project}/{spec}/pr` call: the MCP caller already knows it at create time; the attach route stays for AIFactory.

## Risks

- Private repos need `GITHUB_TOKEN`/`GH_TOKEN` in the TFactory pod; without it clone fails and the task fails closed with `source_checkout_failed`. Owner step.
- Fail-closed empty tree also applies to a local empty root, which previously proceeded; intended.
- Known and left alone: the per-spec fetch ref is `refs/tfactory/specs/<spec_id>` with no project component, so the same spec id ingested into two projects of one repo at the same moment can race on the ref. Renaming it also touches `liveness_sweep.py`; file a follow-up instead of widening this change. Sequential use (the campaign case) is unaffected.
- Org-scoped keys cannot create projects via ingest; they must register the project first.
- `task_rerun` remains local-only in cluster mode.

## Verification

Tests that fail without the change:
- Backend: `source_tree_empty` for an empty root and no planner call (new); `pr_number` in `source.json` (new); `git_env` reaches the fetch subprocess env (new); unresolvable ref stays `source_checkout_failed` (regression check).
- Route: `repo` + `pr_number` forwarded; clone URL derived from `repo`; `git_url`/`repo` disagreement is 400; `clonedFrom` mismatch is 409; known project plus `repo` ignores the registered path; clone failure is 502 and creates no task; token comes from env into `git_env` only.
- Proxy (real FastAPI app with a test key, not mocks of the route): ingest and spec-tasks are scope-gated; a foreign-org key is denied by project id and by project name; org-scoped key on an unknown project is 404; malformed ids are rejected; spec-tasks returns the right task for the same spec id in two projects; ingest writes an audit row.
- MCP: with `TFACTORY_API_URL` set the tools call `/api/specs/ingest` and `/api/spec-tasks` (fake `http_client.request`) with the new parameters; `portal_url` follows the env; `repo` with no env errors; local ambiguity returns an error and `project_id` resolves it.
- Existing tests in `tests/test_mcp_task_control.py`, `tests/test_spec_ingest_workspace.py`, `tests/test_specs_ingest_*`, `tests/test_mcp_stdio_*` still pass.
