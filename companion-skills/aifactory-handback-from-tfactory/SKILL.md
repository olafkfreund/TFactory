---
name: handback-to-aifactory
description: From an AIFactory project, receive a correction hand-back from TFactory (sister project). When TFactory's tests found problems, this previews the prepared correction and applies it to the original spec via AIFactory's QA Fixer (writes QA_FIX_REQUEST.md + runs the fixer). The reverse of /handover-to-tfactory.
when_to_use: When TFactory finished testing an AIFactory feature, found failures, and you want AIFactory to fix the code. Triggers — "apply the tfactory correction", "/handback-to-aifactory", "have aifactory fix what tfactory found".
allowed-tools:
  - mcp__aifactory__task_apply_correction
  - mcp__aifactory__task_status
  - mcp__tfactory__task_status
  - Bash
  - Read
---

# /handback-to-aifactory (AIFactory companion)

This skill lives **inside an AIFactory project** and is the user-facing trigger
for applying a TFactory correction to a spec. It is the mirror image of
`TFactory/.claude/skills/handback-to-aifactory/`. Both share the same
procedure; the only difference is which repo the slash command is typed from.

It pairs with `/handover-to-tfactory` to close the loop:

```
/handover-to-tfactory → TFactory tests → (failures) → /handback-to-aifactory
   → AIFactory QA Fixer fixes the spec → re-run TFactory to verify
```

## Installation

From the **AIFactory repo root**:

```bash
mkdir -p .claude/skills/handback-to-aifactory
cp /path/to/TFactory/companion-skills/aifactory-handback-from-tfactory/SKILL.md \
   .claude/skills/handback-to-aifactory/SKILL.md
```

The receiver this skill drives ships in AIFactory itself (issue
`olafkfreund/AIFactory#317`): the REST route
`POST /api/tasks/{task_id}/apply-correction` and the MCP tool
`task_apply_correction`. Make sure AIFactory's web-server is running (port
3101) and — for the MCP path — that AIFactory's own MCP server is registered in
your Claude Code MCP config so `mcp__aifactory__task_apply_correction` resolves.

## When to use

- explicit `/handback-to-aifactory`
- "apply the tfactory correction"
- "have aifactory fix what tfactory found"

## Procedure

### 1. Get the correction payload

The correction request lives in the **TFactory** workspace for the task:
`~/.tfactory/workspaces/<project_id>/specs/<spec_id>/findings/handback_request.md`
(+ `.json`). If you have it, read it; if TFactory is on another host, have the
operator paste the `handback_request.md` contents.

### 2. Identify the target spec

From `handback_request.json`, read `aifactory_task_id` — it's
`<project_id>:<spec_id>` for the AIFactory spec to fix.

### 3. Preview, then apply

```
mcp__aifactory__task_apply_correction(
  project_id=..., spec_id=...,
  fix_request_md=<contents of handback_request.md>,
  source=<"triage" | "visual_inspection">,
  confirm=false        # preview — writes/starts nothing
)
```

Show the operator the preview (`would_write`). On a clear yes, call again with
`confirm=true` — AIFactory writes `QA_FIX_REQUEST.md` onto the spec and runs the
QA Fixer.

### 4. Report + (optionally) re-test

Report the spec + returned status (e.g. `qa_fixing`). When the fixer finishes,
re-run TFactory to verify the fix: `mcp__tfactory__task_rerun(task_id=...)`.

## Failure modes

- **Spec not found (404)** → the `aifactory_task_id` doesn't match a current
  spec; check it was handed over from this project.
- **MCP tool missing** → register AIFactory's MCP server, or run the apply from
  the TFactory side: `cd apps/backend && python -m agents.handback <spec_dir> --send`.
- **Fixer needs creds** → the QA Fixer runs a real provider; ensure AIFactory's
  LLM provider is configured.
