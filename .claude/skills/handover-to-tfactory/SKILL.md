---
name: handover-to-tfactory
description: Hand a finished AIFactory spec off to TFactory for autonomous test generation. Records the task, snapshots the spec dir, and drives the full Planner → Gen-Functional → Executor → Evaluator → Triager pipeline to produce a triage report + (optionally) commit tests to the feature branch + post a PR comment.
when_to_use: When the user has finished an AIFactory feature on a branch and wants TFactory to generate aligned pytest tests + a verdicts/coverage report. Common triggers — "hand this off to tfactory", "/handover-to-tfactory", "generate tests for the current PR", "have tfactory test this spec".
allowed-tools:
  - mcp__tfactory__project_list
  - mcp__tfactory__project_create
  - mcp__tfactory__task_create_and_run
  - mcp__tfactory__task_status
  - mcp__tfactory__task_list
  - mcp__tfactory__report_get
  - mcp__tfactory__task_rerun
  - Bash
---

# /handover-to-tfactory

Hand a finished AIFactory spec off to TFactory.

> **Status (post-MVP, v0.1.0-mvp):** the full 4-agent pipeline is wired
> and tested against mocked SDK + injected docker-runner seams. Real
> end-to-end run against an AIFactory project still requires an
> `ANTHROPIC_API_KEY` + a running Docker daemon + a real git/gh setup.
> See `guides/e2e-smoke.md` for the operator-facing walkthrough.
>
> The skill records the task, snapshots the AIFactory spec into
> `~/.tfactory/workspaces/<proj>/specs/<spec>/context/`, and (with
> `TFACTORY_AUTO_*=1`, the production default) auto-fires the pipeline.
> Final status reaches `triaged` / `triaged_empty` when the Triager
> finishes; `findings/triage_report.md` holds the human-readable report.

## When to use

Trigger this skill when the user signals "ship the tests" or "have
tfactory test this":

- explicit `/handover-to-tfactory`
- "hand this over to tfactory"
- "generate tests for spec X"
- "have tfactory cover this PR"

If the user is mid-feature and the branch isn't ready, push back rather
than handing over a half-built thing.

## Procedure

### 1. Gather the four required arguments

The TFactory MCP tool needs `project_id`, `spec_id`, `branch`, and
`base_ref`. Infer from the conversation + git state; ask only what's
missing.

| Argument | How to determine |
|---|---|
| `project_id` | The AIFactory project ID. Look in the conversation, the AIFactory portal, or `~/.aifactory/projects.json`. If still unclear, ask. |
| `spec_id` | The AIFactory spec ID (the directory name under `~/.aifactory/workspaces/{project_id}/specs/`). Often visible in the recent commits or chat. |
| `branch` | Current branch from `git rev-parse --abbrev-ref HEAD`. |
| `base_ref` | The PR base. Default to `main`; use `git merge-base HEAD origin/main` for the actual fork point if the user pushed back. |

### 2. Confirm the project is registered with TFactory

Call `mcp__tfactory__project_list`. If the AIFactory project isn't in
the result, register it:

```
mcp__tfactory__project_create(
  id=<project_id>,
  name=<human-readable name>,
  root_path=<absolute path to the local checkout>
)
```

### 3. Preview the handover before committing

Call `task_create_and_run` with `confirm=false` first:

```
mcp__tfactory__task_create_and_run(
  project_id=...,
  spec_id=...,
  branch=...,
  base_ref=...,
  confirm=false
)
```

The response contains the `would_create` workspace path. Show this to
the user. Wait for their go-ahead (or interpret a clear yes from the
trigger phrase: "yes, hand it over").

### 4. Create the task for real

Call the same tool with `confirm=true`. The response contains:

- `task_id` — record this verbatim
- `spec_dir` — the TFactory workspace path (e.g.
  `~/.tfactory/workspaces/<project>/specs/<id>/`)
- `portal_url` — `http://localhost:3102/tasks/<task_id>` (the portal
  ships in Tasks 9-10; until then the URL is a placeholder)

### 5. Report cleanly back to the user

A one-line summary at minimum, e.g.:

> Task `<task_id>` created in TFactory. Workspace at `<spec_dir>`.
> Pipeline execution lands in Tasks 5-8; for now the task sits at
> `status=pending`. Poll with `mcp__tfactory__task_status` once the
> pipeline is wired.

If the user wants progress: call `task_status` once after a beat.

### 6. (Optional) Fetch the report when ready

When pipeline tasks (5-8) are landed, the Triager writes `report.md` +
`report.json` into the workspace. Fetch with:

```
mcp__tfactory__report_get(task_id=<task_id>, format='md')
```

## Failure modes

- **Unknown project** → `project_list` is empty or doesn't contain the
  id. Walk the user through `project_create` first.
- **Spec already handed over** → `task_create_and_run` errors with
  "spec_dir already exists". Offer `task_rerun` instead.
- **TFactory MCP server not reachable** → the tool call times out. Tell
  the user to start the server: `scripts/start-tfactory-mcp.sh` from
  the TFactory repo root. (This skill assumes the AIFactory project's
  `.mcp.json` registers TFactory — see the companion skill at
  `companion-skills/aifactory-handover-to-tfactory/` in the TFactory
  repo for installation steps.)

## Non-goals at MVP

- This skill does **not** drive the Planner / Generators / Executor /
  Evaluator / Triager directly — those agents run inside the TFactory
  backend once Tasks 5-8 land.
- This skill does **not** push code or open PRs. The Triager (Task 8)
  handles `git commit` + `gh pr comment` once the pipeline is wired.
- This skill does **not** handle external repositories (non-AIFactory).
  TFactory MVP is spec-aware-handover only; arbitrary-repo testing is
  out of scope.
