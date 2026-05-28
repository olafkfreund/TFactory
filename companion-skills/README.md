# Companion skills

Skills that live in **other repos** but consume TFactory's MCP server.
Kept here as the canonical source — copy them into the target repo,
don't symlink (Claude Code reads from each repo's `.claude/skills/`
independently).

## What's here

- [`aifactory-handover-to-tfactory/`](./aifactory-handover-to-tfactory/SKILL.md)
  — the user-facing `/handover-to-tfactory` skill that lives inside
  an AIFactory project. Mirrors `TFactory/.claude/skills/handover-to-tfactory/`
  but installs at `AIFactory/.claude/skills/handover-to-tfactory/`.

## How to install one

Each subdirectory has an `SKILL.md` with install steps at the top.
Generally:

```bash
# from the target repo's root
mkdir -p .claude/skills/<skill-name>
cp /path/to/TFactory/companion-skills/<skill-name>/SKILL.md \
   .claude/skills/<skill-name>/SKILL.md
```

Then register TFactory's MCP server in the target repo's `.mcp.json`
(or your user-level Claude Code MCP config). See the individual SKILL
file for the exact JSON snippet.

## Why not just symlink?

Claude Code reads `.claude/skills/` independently per repo. Symlinking
across repos is brittle (worktrees, container mounts, CI), and a
canonical-source-of-truth copy is easier to update via PR.
