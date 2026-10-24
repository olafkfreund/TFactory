---
status: approved
issue: 1430
spec: spec/2026-10-24-1430-handback-triage-everywhere.md
---

# Plan: Send triage on every TFactory handback path

Approved decisions, copied from the spec:

- **D1** Every handback skill sends with
  `cd apps/backend && python -m agents.handback <spec_dir> --send`. It goes
  through `send_correction` (`apps/backend/agents/handback/send.py`), which
  already sends `triage`, `manifest_hash` and `correlation_key`.
  Confirm-first: preview with `python -m agents.handback <spec_dir>` (dry run),
  get the operator's yes, then run `--send`.
- **D2** `mcp__aifactory__task_apply_correction(...)` stays only as a
  fallback for when `--send` cannot run. It sits after the CLI path, and the
  text next to it says it sends markdown only and that AIFactory rejects it
  once `APP_REQUIRE_CORRECTION_TRIAGE` is on.
- **D3** The files are the three SKILL.md files and `guides/aifactory-handback.md`.
  `tfactory-fixloop` drops the MCP tool from `allowed-tools`.
  `handback-to-aifactory` keeps it, because its fallback still uses it.
- **D4** The guard test goes in `tests/test_skills.py`.
- No runtime code changes. No AIFactory change. Do not touch `send.py`,
  `__main__.py` or `trigger.py`.

Use this warning text in each fallback (same wording everywhere):

> Fallback only, for when `python -m agents.handback <spec_dir> --send`
> cannot run here. The AIFactory MCP tool sends the markdown without the
> `triage` block, so AIFactory will reject it once
> `APP_REQUIRE_CORRECTION_TRIAGE` is on.

## Steps

1. `tests/test_skills.py` (append after line 283): add the guard tests.
   - `HANDBACK_SEND = "agents.handback <spec_dir> --send"`
   - `test_apply_correction_calls_are_marked_fallback`: parametrize over
     `sorted((REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md")) +
     sorted((REPO_ROOT / "companion-skills").glob("*/SKILL.md"))` (use
     `ids=` from the parent dir name). If `"task_apply_correction("` is in
     the text, assert `"APP_REQUIRE_CORRECTION_TRIAGE"` is in the text and
     `0 <= text.find(HANDBACK_SEND) < text.find("task_apply_correction(")`.
   - `test_fixloop_does_not_allow_markdown_only_tool`: parse the
     front-matter the same way the file's existing tests do (reuse its helper
     near line 60-90). Assert `"mcp__aifactory__task_apply_correction"` is
     not in `allowed-tools`.
   → verify by running `PYTHONPATH=apps/backend pytest tests/test_skills.py -q -k "apply_correction or fixloop"`.
   It must FAIL (red) for handback-to-aifactory, aifactory-handback-from-tfactory and fixloop.
   Traps: `companion-skills/` is at the repo root, not under `.claude/`. Some
   skill dirs may have no SKILL.md, which is fine because glob skips them. Do
   not change the existing 4-skill parametrization.

2. `.claude/skills/handback-to-aifactory/SKILL.md:81-112` (step 4): reorder.
   **a) Via the TFactory CLI (preferred, sends triage):** preview with
   `cd apps/backend && python -m agents.handback <spec_dir>`, show the
   operator the printed payload and target, and on a clear yes run
   `... --send`. Keep the existing paragraph on the `api_url` /
   `TFACTORY_AIFACTORY_API_URL` POST under a).
   **b) Fallback: the AIFactory MCP tool:** the existing
   `task_apply_correction` block (confirm=false, then true), headed by
   the warning text. Leave `allowed-tools` line 6 alone. Also check the
   description/when_to_use front-matter and the "Failure modes" section
   (line 132+) for wording that calls MCP preferred, and fix it.
   → verify by running `grep -n "preferred\|APP_REQUIRE_CORRECTION_TRIAGE" .claude/skills/handback-to-aifactory/SKILL.md`.
   Traps: keep step 5 "Report back" valid. The CLI prints `result.to_dict()`
   JSON, so the report reads the status from that JSON.

3. `.claude/skills/tfactory-fixloop/SKILL.md:9`: delete the line
   `  - mcp__aifactory__task_apply_correction`. The body (step 3, lines
   ~80-95) already uses `--send`. Leave it alone.
   → verify by running `grep -c task_apply_correction .claude/skills/tfactory-fixloop/SKILL.md`, which must print `0`.
   Traps: none.

4. `companion-skills/aifactory-handback-from-tfactory/SKILL.md`:
   - lines 36-42 (Installation): say the preferred send is the TFactory CLI
     on the TFactory host, and the MCP registration is only needed for the
     fallback.
   - lines 64-77 (step 3): first the CLI path (preview, yes, `--send`, run
     in the TFactory checkout's `apps/backend`). Then the MCP block as
     "Fallback" with the warning text.
   - lines 86-89 (Failure modes, "MCP tool missing"): reword so the CLI is
     the normal path, not the rescue.
   → verify by running `grep -n "APP_REQUIRE_CORRECTION_TRIAGE\|--send" companion-skills/aifactory-handback-from-tfactory/SKILL.md`.
   Traps: this file gets copied into AIFactory checkouts, so write paths as
   "the TFactory checkout's `apps/backend`", not repo-relative `cd apps/backend` alone.

5. `guides/aifactory-handback.md:64-72` (path A): say the skill sends with
   `python -m agents.handback <spec_dir> --send` after a preview and your
   yes, and that the MCP tool `task_apply_correction` is a markdown-only
   fallback that AIFactory rejects once `APP_REQUIRE_CORRECTION_TRIAGE` is
   on. Path B stays as it is.
   → verify by reading the section.
   Traps: none.

6. Run the full skill tests.
   → verify by running `PYTHONPATH=apps/backend pytest tests/test_skills.py tests/test_tfactory_routes_skills.py tests/test_well_known_agent_skills.py -q`. All must pass.
   Traps: the pre-commit hooks can take 10+ minutes, so use long timeouts.
   Never use `--no-verify` on this commit.

## Tests

- `PYTHONPATH=apps/backend pytest tests/test_skills.py -q`: red after step 1,
  green after step 4.
- `git grep -n "task_apply_correction(" -- '*/SKILL.md'`: every hit is under
  a fallback heading that names the flag.

## Rollback

Revert the commit. It is docs plus one test file, with no runtime or data impact.
