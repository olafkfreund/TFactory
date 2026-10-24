---
status: draft
issue: 1430
intent: intent/2026-10-24-1430-handback-triage-everywhere.md
---

# Spec: Send triage on every TFactory handback path

## Design

- **D1 One send path.** Every handback skill sends with
  `cd apps/backend && python -m agents.handback <spec_dir> --send`.
  That calls `send_correction` in `apps/backend/agents/handback/send.py`,
  which already adds `triage`, `manifest_hash` and `correlation_key`.
  Confirm-first stays as it is: the agent previews with
  `python -m agents.handback <spec_dir>` (dry run, prints the payload and
  target), asks the operator, and only then runs `--send`.
- **D2 Fallback, marked.** The `mcp__aifactory__task_apply_correction(...)`
  block stays, but only as a fallback for when `--send` cannot run (no TFactory
  checkout or backend env on this host). It sits after the CLI path, and next
  to it the skill says that it sends markdown only and that AIFactory rejects
  it once `APP_REQUIRE_CORRECTION_TRIAGE` is on.
- **D3 Files.**
  - `.claude/skills/handback-to-aifactory/SKILL.md` step 4: swap a) and b),
    so the CLI is a) and preferred, and the MCP call is b), the fallback with
    the D2 warning. Keep `mcp__aifactory__task_apply_correction` in
    `allowed-tools` because the fallback still uses it.
  - `.claude/skills/tfactory-fixloop/SKILL.md`: drop
    `mcp__aifactory__task_apply_correction` from `allowed-tools`. The skill
    never calls it directly; it sends through `/handback-to-aifactory` or
    through the `--send` block it already has.
  - `companion-skills/aifactory-handback-from-tfactory/SKILL.md` step 3: the
    preferred path is the TFactory CLI (preview, then `--send`), run on the
    TFactory host. The MCP block becomes the fallback with the D2 warning.
    The "MCP tool missing" failure mode is reworded to match.
  - `guides/aifactory-handback.md` path A: say that the skill sends with the
    CLI (`--send`) and that MCP is only the marked fallback.
- **D4 Guard test.** Add the test to the existing `tests/test_skills.py`.
  It globs `.claude/skills/*/SKILL.md` and
  `companion-skills/*/SKILL.md`. For each file whose text contains
  `task_apply_correction(`, it asserts two things: the text contains
  `APP_REQUIRE_CORRECTION_TRIAGE`, and `agents.handback <spec_dir> --send`
  comes before the first `task_apply_correction(`. A second assert checks
  that the fixloop `allowed-tools` does not list
  `mcp__aifactory__task_apply_correction`.

## Alternatives rejected

- **Add triage, manifest_hash and correlation_key to AIFactory's MCP tool, and
  have the skills pass `handback_request.json`.** This needs a change in
  AIFactory and a schema change to the tool, and the agent would have to copy a
  large JSON blob into a tool call. The CLI already builds that payload from
  the workspace, so this adds no value now. The decision is recorded in the
  intent.
- **Delete the MCP path entirely.** It would strand a host that can reach
  AIFactory's MCP but has no TFactory backend env. Keeping it with a clear
  warning costs nothing until the flag flips.
- **A new test file.** `tests/test_skills.py` already does structural checks
  on SKILL.md files, so the guard goes there.

## Risks

- The CLI needs `apps/backend` deps and network access to AIFactory's
  `api_url`. That is already the documented fallback, so no new
  requirement. If it fails, the operator falls back to MCP and gets the warning.
- The companion skill runs from an AIFactory checkout. Its CLI path needs the
  TFactory checkout path. The text says to run it on the TFactory side, as the
  failure mode already does.
- Docs only. No runtime behaviour changes, so there is nothing to roll out.

## Verification

- `PYTHONPATH=apps/backend pytest tests/test_skills.py -q` passes.
- The new test fails against `origin/dev`'s skill text. Check this by
  running it before the doc edits (red), then after (green).
- `git grep -n "task_apply_correction(" -- '*.md'`: every SKILL.md hit sits
  under a fallback heading that names `APP_REQUIRE_CORRECTION_TRIAGE`.
