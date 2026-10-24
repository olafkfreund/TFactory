---
status: draft
issue: 1430
author: olafkfreund
---

# Intent: Send triage on every TFactory handback path

## Problem

AIFactory#1858 adds `APP_REQUIRE_CORRECTION_TRIAGE`. When it is on,
`POST /api/tasks/{id}/apply-correction` rejects a correction that has no
`triage` block. It ships off because some TFactory handback paths still send
markdown only.

The code sender is fine: `apps/backend/agents/handback/send.py`
(`send_correction`, used by `trigger.py` and by
`python -m agents.handback <spec_dir> --send`) already sends `triage`,
`manifest_hash` and `correlation_key`.

The skills are not. Three of them tell the agent to call
`mcp__aifactory__task_apply_correction` with `fix_request_md` only, and that
AIFactory MCP tool has no `triage` input at all:

- `.claude/skills/handback-to-aifactory/SKILL.md` step 4a, labelled "preferred"
- `.claude/skills/tfactory-fixloop/SKILL.md`, through `/handback-to-aifactory`
  and its `allowed-tools`
- `companion-skills/aifactory-handback-from-tfactory/SKILL.md` step 3

`guides/aifactory-handback.md` path A says the same thing to operators.

## Proposed outcome

- Every skill and the guide send the correction with
  `python -m agents.handback <spec_dir> --send`, so triage always goes with it.
- The MCP markdown-only call stays only as a fallback for when `--send` cannot
  run. It is clearly marked as a fallback that AIFactory will reject once
  `APP_REQUIRE_CORRECTION_TRIAGE` is on.
- A test fails if a skill brings back a bare markdown-only
  `task_apply_correction` call without that marking.
- AIFactory can then turn the flag on (a follow-up on AIFactory#1858, out of scope here).

## Affected users and systems

- Agents running `/handback-to-aifactory`, `/tfactory-fixloop`, and the
  companion skill installed in an AIFactory checkout.
- Operators reading `guides/aifactory-handback.md`.
- No runtime code changes. No AIFactory change.

## Constraints

- Docs and tests only. `send.py`, `__main__.py` and the AIFactory tool stay as they are.
- Keep confirm-first. The preview is `python -m agents.handback <spec_dir>`
  (dry run). The send needs `--send` and an explicit yes from the operator.
- Smallest change. Extend the existing `tests/test_skills.py`.

## Open questions

- Should we switch the skills to the existing `--send` CLI, or add
  `triage` / `manifest_hash` / `correlation_key` to AIFactory's MCP tool?
  **Decided (lead recommendation, user-approved 2026-10-24):** switch the three
  skills to `python -m agents.handback <spec_dir> --send`, which already sends
  all three fields. Keep the MCP call only as a fallback for when `--send` is
  unavailable, marked as rejected once `APP_REQUIRE_CORRECTION_TRIAGE` is on.
  No AIFactory change.
