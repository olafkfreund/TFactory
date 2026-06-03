---
name: handover-to-tfactory
description: From an AIFactory project, hand a finished spec off to TFactory (sister project) for autonomous test generation. Records the task with TFactory's MCP server; once TFactory Tasks 5-8 land, this also drives the planner→generator→executor→evaluator→triager pipeline.
when_to_use: When the user has finished an AIFactory feature on a branch and wants TFactory to generate aligned pytest tests + a coverage/security report. Common triggers — "hand this off to tfactory", "/handover-to-tfactory", "have tfactory test this spec", "generate tests for the current PR".
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

# /handover-to-tfactory (AIFactory companion)

This skill lives **inside an AIFactory project** and is the user-facing
trigger for handing a finished AIFactory spec off to TFactory.

It is the mirror image of `TFactory/.claude/skills/handover-to-tfactory/`.
Both files share the same procedure; the only practical difference is
which repo the slash command is typed from. This one installs into
`AIFactory/.claude/skills/handover-to-tfactory/SKILL.md`.

## Installation

From the **AIFactory repo root**:

```bash
mkdir -p .claude/skills/handover-to-tfactory
cp /path/to/TFactory/companion-skills/aifactory-handover-to-tfactory/SKILL.md \
   .claude/skills/handover-to-tfactory/SKILL.md
```

Then register TFactory's MCP server in AIFactory's `.mcp.json` (or your
user-level Claude Code MCP config) so the `mcp__tfactory__*` tools are
reachable. Typical project-scoped form:

```json
{
  "mcpServers": {
    "tfactory": {
      "type": "stdio",
      "command": "bash",
      "args": [
        "/absolute/path/to/TFactory/scripts/start-tfactory-mcp.sh"
      ],
      "env": {
        "TFACTORY_PROJECT_DIR": "${CLAUDE_PROJECT_DIR:-.}",
        "TFACTORY_API_URL": "http://localhost:3102",
        "TFACTORY_WORKSPACE_ROOT": "~/.tfactory"
      }
    }
  }
}
```

Once both files are in place, `/handover-to-tfactory` is available
inside Claude Code sessions opened in the AIFactory project.

## When to use

Trigger when the user signals "ship the tests" or "have tfactory cover
this":

- explicit `/handover-to-tfactory`
- "hand this over to tfactory"
- "generate tests for spec X"
- "have tfactory test this PR"

If the user is mid-feature and the branch isn't ready, push back rather
than handing over a half-built thing.

## Procedure

### 1. Gather the four required arguments

The TFactory MCP tool needs `project_id`, `spec_id`, `branch`,
`base_ref`. Infer from conversation + git state; only ask for what's
missing.

| Argument | How to determine |
|---|---|
| `project_id` | The AIFactory project ID. Visible in `~/.aifactory/projects.json`, in the portal URL, or in the active spec's path. |
| `spec_id` | The AIFactory spec ID — the directory name under `~/.aifactory/workspaces/<project_id>/specs/`. Usually obvious from recent chat or `ls ~/.aifactory/workspaces/<project_id>/specs/`. |
| `branch` | `git rev-parse --abbrev-ref HEAD`. |
| `base_ref` | The PR base. Default `main`; use `git merge-base HEAD origin/main` if needed. |

### 1b. Ask what to focus on + whether to enable a visual inspection (#170)

Before previewing, ask the user (skip whichever is already clear):

1. **What should TFactory focus on?** — the task intent / acceptance focus.
2. **Enable a visual inspection?** — for UI-heavy features (or a SaaS target like
   ServiceNow), TFactory can record a Playwright **browser** run, capture per-step
   verification + error screenshots, and package a human **visual-inspection
   report** + correction plan into `automated-test/<datetime>/` (surfaced in the
   portal's *Visual Reports*). If yes, gather the **visual target** name (a
   `visual: true` target in `.tfactory.yml`) and the **flow** to inspect.

Pass these as the optional `visual_inspection` argument to `task_create_and_run`:
`{ "enabled": true, "target": "<name>", "flow": "<what to inspect>" }`. Omit it for
a normal code-test task — the default path is unchanged.

### 2. Confirm the project is registered with TFactory

Call `mcp__tfactory__project_list`. If the AIFactory project isn't
present, register it:

```
mcp__tfactory__project_create(
  id=<aifactory project_id>,
  name=<human readable name>,
  root_path=<absolute path to local checkout>
)
```

### 3. Preview, then commit

First `task_create_and_run` with `confirm=false` for the preview. Show
the workspace path to the user. On confirmation, call again with
`confirm=true`. Capture and report `task_id`, `spec_dir`, `portal_url`.

### 4. Report and (optionally) poll

A one-line summary back to the user. If they want progress, call
`task_status` once after a beat. Once Tasks 5-8 land, the Triager will
have written `report.md`/`report.json` — fetch with `report_get`.

## Failure modes

- **Unknown project** → walk the user through `project_create` first.
- **Spec already handed over** → offer `task_rerun` instead (MVP only
  supports the `functional` lane).
- **TFactory MCP server not reachable** → the user needs to start it
  via `scripts/start-tfactory-mcp.sh` in the TFactory repo, and confirm
  the AIFactory `.mcp.json` points at the right absolute path.

## When the tests find problems — hand back for a fix

If TFactory's run finishes with failing tests / rejects, you can hand the
problems back to AIFactory for a fix with **`/handback-to-aifactory`** (install
its companion from `TFactory/companion-skills/aifactory-handback-from-tfactory/`).
It applies the correction TFactory prepared to the original spec via AIFactory's
QA Fixer, closing the loop:

```
/handover-to-tfactory → test → (failures) → /handback-to-aifactory
   → AIFactory QA Fixer → re-run TFactory to verify
```

## Status at MVP

Workspace creation + status tracking work. The pipeline (planner →
generators → executor → evaluator → triager) is scheduled for TFactory
Tasks 5-8; until then `task_create_and_run` records the task with
`status=pending` and you can introspect via `task_status` /
`task_list`.
