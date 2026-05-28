# The `/handover-to-tfactory` companion skill

> Reference doc for sub-task 12.4. The actual skill definition lives in
> [`.claude/skills/handover-to-tfactory/SKILL.md`](../.claude/skills/handover-to-tfactory/SKILL.md);
> this guide explains what it does, where it lives on the AIFactory side,
> and how operators wire it up.

The `/handover-to-tfactory` skill is the operator-facing on-ramp to the
TFactory pipeline. A Claude Code session in an AIFactory repo invokes
this skill (via `/handover-to-tfactory` or natural language like "hand
this off to tfactory") and the four-agent pipeline takes over.

## Where it lives

The skill is checked into THIS repo at
`.claude/skills/handover-to-tfactory/SKILL.md`. There are two ways
AIFactory sessions can discover it:

### Option 1 — symlink from AIFactory (recommended)

In the AIFactory repo:

```bash
mkdir -p .claude/skills
ln -s /path/to/TFactory/.claude/skills/handover-to-tfactory \
      .claude/skills/handover-to-tfactory
```

Pros: the AIFactory side always sees the canonical TFactory version;
updating the skill in TFactory propagates automatically.

### Option 2 — copy from AIFactory

```bash
cp -r /path/to/TFactory/.claude/skills/handover-to-tfactory \
      AIFactory/.claude/skills/handover-to-tfactory
```

Pros: independent of TFactory checkout location. Cons: drifts if the
canonical version updates.

## Required MCP server

The skill's `allowed-tools` list references `mcp__tfactory__*` tools.
These come from the TFactory MCP server at
`apps/backend/mcp_server/tfactory_server.py`. The AIFactory repo's
`.mcp.json` (or the user's `~/.claude.json`) must register it:

```json
{
  "mcpServers": {
    "tfactory": {
      "command": "python",
      "args": [
        "-m", "apps.backend.mcp_server.tfactory_server"
      ],
      "cwd": "/path/to/TFactory",
      "env": {
        "PYTHONPATH": "/path/to/TFactory/apps/backend"
      }
    }
  }
}
```

The TFactory repo ships its own `.mcp.json` at the root with the
canonical entry; copy that block into the AIFactory repo's `.mcp.json`
or your user-level one.

## What the skill does (one-line summary)

1. Resolves the current AIFactory project on disk and looks it up (or
   creates it) via `mcp__tfactory__project_create`.
2. Calls `mcp__tfactory__task_create_and_run` with the project_id,
   branch, base_ref, and root_path. The backend's
   `task_control.task_create_and_run` then:
   - Calls the snapshotter (Task 3) to freeze the AIFactory spec dir,
     plan.json, and `git diff base_ref..branch` into
     `~/.tfactory/workspaces/<proj>/specs/<spec>/context/`.
   - Writes `status.json` with `status=pending`.
   - With `TFACTORY_AUTO_PLAN=1` (default), schedules the Planner.
3. The pipeline auto-advances Planner → Gen-Functional → Evaluator →
   Triager. The user polls via `mcp__tfactory__task_status` or watches
   the portal at `:3102`.

## What it does NOT do

- It does **not** post a PR comment by default (per CLAUDE.md
  "no automatic pushes" — `TFACTORY_TRIAGER_PR_COMMENT=1` to opt in).
- It does **not** commit tests to the AIFactory branch by default
  (`TFACTORY_TRIAGER_GIT_WRITE=1` to opt in).
- It does **not** require any Claude API key on the AIFactory side —
  the keys live in TFactory's environment because TFactory's agents are
  the ones calling out.

## Verifying the skill in AIFactory

After symlinking or copying, in an AIFactory Claude Code session:

```bash
# 1. Confirm the skill is discoverable
ls .claude/skills/handover-to-tfactory/SKILL.md

# 2. Confirm the MCP server is reachable
# In the session, type "/" — handover-to-tfactory should appear
```

Then invoke it: `/handover-to-tfactory` (or "hand this off to tfactory").
The skill will respond with the workspace path and a status URL.

## Related

- [`.claude/skills/handover-to-tfactory/SKILL.md`](../.claude/skills/handover-to-tfactory/SKILL.md) — the canonical skill file
- [`apps/backend/mcp_server/tfactory_server.py`](../apps/backend/mcp_server/tfactory_server.py) — MCP server exposing `mcp__tfactory__*` tools
- [`apps/backend/agents/tools_pkg/tools/task_control.py`](../apps/backend/agents/tools_pkg/tools/task_control.py) — `task_create_and_run` implementation + Planner auto-fire scheduler
- [`guides/HANDOVER_WORKFLOW.md`](HANDOVER_WORKFLOW.md) — operator-facing flow doc (AIFactory user → TFactory autonomous build)
- [`guides/CLAUDE_CODE_MCP_TOOLS.md`](CLAUDE_CODE_MCP_TOOLS.md) — full MCP tool reference
- [`guides/e2e-smoke.md`](e2e-smoke.md) — 9-scenario manual smoke runner
