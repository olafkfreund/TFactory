---
status: approved
issue: 1574
spec: spec/2026-11-18-1574-tf-verify-branch.md
coders:
  1: codex   # small tricky logic in one function (empty-tree gate, git_env, pr_number)
  2: coder   # lead (Claude) writes the REST route changes: security-sensitive, needs full context
  3: codex   # contained edit: three new proxy routes following the neighbours' pattern
  4: coder   # lead (Claude) writes the MCP tool changes: spans one 1400-line file with many call sites
  5: codex   # tests for steps 1 and 3, written from the spec's list
reviewers:
  - codex    # independent read-only final review of the diff (agy is off by owner decision)
---

# Plan: verify a cluster-built branch over MCP (#1574, #1583)

All decisions are those of the approved spec (`spec/2026-11-18-1574-tf-verify-branch.md`). Summary:

- Cluster side: `create_spec_ingest_workspace` records `pr_number`, fails closed on an empty tree (`phase=source_tree_empty`, planner skipped) and accepts `git_env` for the per-spec git network calls. `POST /api/specs/ingest` accepts `pr_number`; a GitHub `repo` selects the checkout via `clone_or_update(repo_url)`; `git_url`, `repo` and the project's `clonedFrom` must agree (400/409); clone failure is a fixed-message 502 with no task; token only through `git_env`.
- New org-authorised, audited proxy routes: `POST /api/mcp-stdio/specs/ingest`, `GET /api/mcp-stdio/spec-tasks`, `GET /api/mcp-stdio/spec-tasks/{project_id}/{spec_id}?report=`. Project id-or-name resolved to the canonical id before `_authorize_key`. Org-scoped key on an unknown project: 404.
- MCP: cluster mode when `TFACTORY_API_URL` is set; new params (`repo`, `branch`, `pr_number`, `spec_text`, `project_id` on status/report/rerun); ambiguity error for duplicate spec ids locally; `portal_url` from env; `tree` in responses; `repo` without the env is an error. Ingest call uses `timeout=660`, no retry.
- Known, not fixed: the per-spec fetch ref has no project component (follow-up issue). `task_rerun` is local-only.

## Steps

1. `apps/backend/agents/tools_pkg/tools/task_control.py`, `create_spec_ingest_workspace` (about 497-735), `_add_spec_worktree` (about 289-382), `_default_branch` (about 385-420): add params `pr_number: int | None = None` and `git_env: dict[str, str] | None = None`; merge `git_env` into the hermetic env of both git helpers; write `pr_number` into `source.json`; after the worktree decision, if `checkout_failed_reason` is unset and `sut_dir` is not a directory or has no entry other than `.git`, set `checkout_failed_reason = "source tree is empty ..."` and use `phase="source_tree_empty"` (else `source_checkout_failed`) in `status.json`; return a `source_sha` and `repo_slug` key in the result dict. Check: `pytest tests/test_spec_ingest_workspace.py -q`.
   Traps: do not rename `refs/tfactory/specs/<spec_id>`; do not log `git_env`; failure keeps `notify_terminal`.
2. `apps/web-server/server/routes/specs.py`, `SpecIngestRequest` and `ingest_spec`: add `pr_number` (gt 0). Add helper `_github_slug(repo_or_url)` returning `owner/name` or None. Rules per the spec: derive `git_url`; `git_url` vs `repo` mismatch 400; `clonedFrom` mismatch 409; known project plus GitHub `repo` uses `clone_or_update(url)` as root; build `git_env` with `_git_askpass_env("x-access-token", token)` around the `create_spec_ingest_workspace` call when a token exists; map `GitOperationError` to 502 with a fixed message via `client_error`; add `tree`, `repo_slug`, `source_sha`, `workspace_project_id` to the response. Check: `pytest tests/test_specs_ingest_clone.py tests/test_specs_ingest_ensure_clone.py apps/web-server/tests/test_specs_route.py -q`.
   Traps: keep existing `git_url`-only and no-`repo` behaviour byte-identical; `safe_component` on ids; never echo git stderr or the token.
3. `apps/web-server/server/mcp_stdio/router.py`: add the three routes after `proxy_create_and_run_task`. Helper `_resolve_project(ref) -> (id, entry)` (id or name, via `load_projects`). Dependencies: ingest `require_acw_scope(TASK_WRITE_SCOPE)`, list and detail `require_acw_scope(MCP_READ_SCOPE)`; then handlers call `_authorize_key` themselves with `Depends(get_db)` (ingest: role member; detail: role viewer; `_authorize_key` checks the project, not the scope); foreign projects filtered in the list route with `_foreign_projects`; detail route reads `_spec_dir(safe_component(pid), safe_component(spec))`: `status.json` plus derived lane progress (`agents.lane_progress.derive_lane_progress`), `context/source.json` fields (`source_sha`, `repo_slug`, `source_branch`, `pr_number`), report body from `findings/triage_report.{md,json}` when `report` is given. Ingest route: 404 for an org-scoped key with an unknown project; otherwise delegates to `ingest_spec`; audit with `ACTION_MCP_TASK_CREATE_AND_RUN`. Check: `pytest tests/test_mcp_stdio_authz.py tests/test_mcp_stdio_proxy.py -q`.
   Traps: audit failures must not fail the route (`_audit_mcp_write`); no new auth scope names.
4. `apps/backend/agents/tools_pkg/tools/task_control.py`, tool factory (about 770-1402): add `_cluster_mode()`, `_portal_url(task_id)`, `_locate(args)` (local: project-aware `_find_task` plus ambiguity error; cluster: via spec-tasks), cluster branches in `project_list`, `task_list`, `task_status`, `report_get`, `task_create_from_spec`, `task_create_and_run`; new input-schema properties; the `confirm` preview branch stays FIRST in both creation tools, before any local or remote mutation; `branch` and `ref` together is an error, `branch` maps to `ref` for from_spec; cluster `create_and_run` requires `spec_text` and maps `branch` to `source_branch`; `repo` without env errors; `task_rerun` takes `project_id` and is not available in cluster mode; `tree` in responses. Check: `pytest tests/test_mcp_task_control.py tests/test_tfactory_mcp_tools.py tests/test_mcp_server.py -q`.
   Traps: `http_client.request` rewrites `/api/...` itself, so pass `/api/specs/ingest` and `/api/spec-tasks`; ingest uses `timeout=660`; update existing tests that assert the old `_find_task` first-match behaviour.
5. Tests, new files `tests/test_verify_branch_ingest.py` (steps 1-2), `tests/test_mcp_stdio_spec_tasks.py` (step 3, reusing the `env` fixture pattern of `tests/test_mcp_stdio_authz.py`), `tests/test_mcp_cluster_mode.py` (step 4, fake `http_client.request`). Cover every bullet under the spec's Verification. Check: `pytest <the three files> -q`, then the full touched set plus `ruff check` and `ruff format --check` on changed files.
   Traps: no network; empty-tree test uses a source dir separate from the workspace root (workspace files are written into `spec_dir` before the check), `schedule=True` with a planner spy, and a repo with an `origin` (a bare `git init` hits the default-branch failure instead); the proxy test enforces scopes per key (not the always-both fixture) and has a viewer-role detail case; assert no ingest call without `confirm=true`; ruff sorts imports plainly (no `as` aliases inside parenthesised imports).

## Tests

- Per-step checks above, then `pytest tests -q -x` limited to the touched modules (full suite if under 10 minutes), `ruff check` and `ruff format --check`.
- Each new failure-detecting test is shown to fail on `origin/dev`: add a disposable `git worktree` of `origin/dev` under the scratchpad, copy the new test files in, run them, remove the worktree. Report which tests are new-behaviour and which are regression checks. Skipped tests are listed (`tests/test_mcp_task_control.py` is skipped wholesale and `tests/test_tfactory_mcp_tools.py` skips without the SDK: the new MCP tests must execute, not skip).
- Lead runs every check itself; Codex output is advice.

## Rollback

Revert the PR. All new behaviour is additive or behind `TFACTORY_API_URL` / a new `repo` parameter. The one default-path change is the empty-tree failure for a spec ingest; reverting restores the old behaviour. No data migration.
