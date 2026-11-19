---
status: approved
issue: 1574
author: olafkfreund
---

# Intent: verify a cluster-built branch over MCP (#1574, #1583)

## Problem

The TFactory MCP tools `task_create_and_run` and `task_create_from_spec` only work against the host's local filesystem. A branch built by the cluster AIFactory cannot be verified from a Claude Code session:

- `task_create_and_run` fails `AIFactory spec dir not found: /home/<user>/.aifactory/workspaces/<project>/specs/<spec>` (the spec lives in the AIFactory pod).
- `task_create_from_spec` has no repo or pr_number parameter. The project `root_path` is a pod path, so the planner fails `Working directory does not exist: /home/nonroot/.aifactory/workspaces/<repo>`.
- The MCP server keeps its own `~/.tfactory/projects.json` and workspaces, split from the cluster pod's store. `project_list`, `task_status`, `report_get` show stale local state, and `portal_url` is `http://localhost:3103` where nothing listens.
- With no checkout, a run is graded against nothing: level 6 produced `source_branch=null`, `repo_slug=null`, status `triaged_empty`, 0/7 verified, 16.60 USD spent.
- `task_status`/`report_get` resolve a task by spec id only (#1583), so the same spec id in two projects returns the wrong task.

All of levels 1 to 9 of the 2026-11-18 e2e campaign hit this.

## Proposed outcome

- The MCP tools accept `repo` (owner/name or URL), `branch`/`ref` and optional `pr_number`. The cluster TFactory clones the repo, checks out the ref in its own per-run worktree, and records `repo_slug`, `source_branch`, `source_sha`, `pr_number` in `source.json`.
- When `TFACTORY_API_URL` is set, the MCP tools use the cluster TFactory for project, task list, status and report, and return a `portal_url` built from that URL.
- An empty source tree fails the task early with a distinct `source_tree_empty` phase before any LLM call. (An unresolvable ref already fails as `source_checkout_failed`; that stays.)
- Status and report lookup take `project_id` plus spec id, so identical spec ids in two projects resolve correctly.
- The remote path goes through new scope-gated, org-authorised, audited routes under `/api/mcp-stdio/` (ingest, spec-task list/status/report). Setting the API URL alone does not enable it: the proxy has no ingest or report route today.
- A `repo` that conflicts with the registered project's recorded origin is rejected, never silently verified against the wrong tree.
- `task_rerun` takes `project_id` too, so a rerun cannot hit the wrong project.
- Without `repo` and without `TFACTORY_API_URL`, behaviour is unchanged, and responses say which tree was used (`tree`: `local` or `cluster_clone`).

## Affected users and systems

Campaign runners and anyone verifying cluster-built branches via MCP. Code: the MCP task-control tools, `http_client.py`, the stdio-MCP proxy router and `POST /api/specs/ingest` in the web-server, and `create_spec_ingest_workspace`.

## Constraints

- No new secret: cloning uses the existing credential path (`clone_or_update` with `GITHUB_TOKEN`/`GH_TOKEN` from the pod environment; the App-token deployment injects those). Private repos need that token to exist in the pod: owner step if it does not.
- Backward compatible for local-path use.
- Out of scope: the zero-tests-ran verdict rule, sandbox changes (team tf-sandbox), lane logic, and the runner image fix (#1573).
- Owner step: set `TFACTORY_API_URL` and `TFACTORY_MCP_KEY` in the MCP client config.

## Open questions

None. Decision (owner delegation 2026-11-18): the cluster TFactory clones the repo itself, as recommended in the issue thread.
