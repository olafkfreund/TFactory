# Workspace layout

Per-task data lives at:

```
~/.tfactory/workspaces/<project_id>/specs/<spec_id>/
```

Override the root with `TFACTORY_WORKSPACE_ROOT=/path/to/dir`.

```
status.json                  ← live status (status / phase / counts)
test_plan.json               ← Planner's lane-tagged subtask plan
context/
  aifactory_spec.md          ← frozen snapshot of the AIFactory spec
  aifactory_plan.json        ← AIFactory's implementation plan (if any)
  diff.patch                 ← base_ref..branch diff
  source.json                ← branch + base_ref + repo metadata
  replan_request.json        ← written by Gen-Functional on guardrail reject
tests/                       ← generated test files (Gen-Functional)
findings/
  verdicts.json              ← Evaluator's per-test verdicts
  triage_report.{md,json}    ← Triager's renderable report
  pr_comment_body.md         ← PR comment body (when no PR# in source.json)
  mutants/                   ← mutate_probe.py's per-test mutants
  COMPLETED.json             ← completion sentinel (opt-in)
  handback_request.{md,json} ← AIFactory correction artifact (opt-in)
logs/
  planner.log · gen_functional.log · evaluator.log · triager.log
```

## status.json — the state machine

`status.json` is the single source of truth for a run. It is updated atomically
with ISO timestamps via `_write_status_patch()`. Stages transition it through:

| Stage | Statuses |
|-------|----------|
| Planner | `planning → planned / stuck` |
| Gen-Functional | `generating → generated / generated_empty / replan_needed / gen_functional_failed` |
| Executor/Evaluator | `evaluating → evaluated` (browser lane adds `executor_app_running` / `app_not_healthy`) |
| Triager | `triaging → triaged / triaged_empty / triager_failed` |

The web-server reads `status.json` to drive the portal; `/tfactory-watch` reads it
(plus the triage report file) over MCP instead of polling.

## Project data vs web-UI data

- `.tfactory/specs/` — per-project data (specs, plans, QA reports, memory). Gitignored.
- `~/.tfactory/` — web-interface data (projects, settings, token, workspaces).

## Worktree isolation

Builds run in an isolated git worktree on a spec branch
(`auto-claude/<spec-name>`). All branches stay **local** until the user explicitly
pushes — there are **no automatic pushes**. `agent_service.py` syncs key files from
the worktree to the main spec dir every 3 seconds so the portal stays live.
