---
status: draft
issue: 1174
intent: intent/2026-09-18-1174-regenerate-bad-imports.md
---

# Spec: Stop generated tests shipping imports that resolve to nothing

Decisions carried from intent review: **both** a deterministic rewrite (when
unambiguous) and a bounded retry-with-feedback (otherwise); delete the
jest-lane-only mapper rule **after** a measured run shows generation no
longer needs it (separate, later change — not in this PR).

## Findings (dev, `apps/backend/agents/gen_functional.py`)

- `_generate_one_subtask` (`:911`) runs one agentic SDK session per subtask;
  the agent **writes the test file itself** (`:963` checks it exists). So a
  retry is one more `_invoke_session` (`:228`, already wall-clock bounded by
  `_GEN_SESSION_TIMEOUT_S`) with a feedback prompt, on the same subtask — no
  Planner involvement.
- `_record_unresolvable_imports` (`:795`) runs at `:977`, **before** the
  source guardrails, and only reports (#1233). Every guardrail rejection goes
  to `_reject_subtask_for_replan` → Planner replan (the #1194 cost).
- `_unresolvable_imports` (`:752`) checks quoted JS/TS specifiers only
  (root-relative with `/`, and `./`/`../` relative). Python imports are the
  pre-flight guard's job (`preflight_static`) and are out of scope.
- **Suspected latent bug:** relative specifiers are resolved against
  `test_path = spec_dir / files[0]` (`:934`), but the jest lane stages the
  test at its authored relative path **inside the project worktree**
  (#1195/4f429933). A correct `../../games/x` would then be reported as
  unresolvable. Harmless while report-only; wrong the moment it drives a
  rewrite or retry. Plan step 1 confirms it with a failing test first.
- The jest lane's `moduleNameMapper` (`nix_env._write_jest_config`) maps
  `^app/(.*)$` → `<rootDir>/$1`, so the bad import passes there and fails in
  the Docker fallback runner — the runner divergence.

## Design

In `_generate_one_subtask`, between reading `source` and the guardrails:

1. **Resolve against the right base.** `_unresolvable_imports` resolves
   relative specifiers against the test's *staged* location,
   `project_dir / files[0]`, not `spec_dir / files[0]`.
2. **Deterministic rewrite (free, first).** For each unresolvable specifier,
   drop leading path segments until the remainder resolves under
   `project_dir` (`app/games/tictactoe/game` → `games/tictactoe/game`). If
   exactly one candidate resolves, rewrite the specifier in the file to the
   path **relative to the staged test location** (`../../games/tictactoe/game`
   style), which node, jest and the Docker runner all resolve without a
   mapper. Zero or several candidates → no rewrite (never guess; same rule as
   #1258's resolver).
3. **Bounded retry-with-feedback (only if still unresolvable).** Re-run the
   session for this subtask with a feedback block naming each bad specifier,
   the file it appeared in, and — when found — the real module path. Budget:
   `TFACTORY_GEN_IMPORT_RETRIES`, default **1**. Each retry re-reads the file
   and re-runs steps 1–2.
4. **After the budget, record and continue** (today's #1233 behaviour): the
   specifier goes to `status.json` `unresolvable_imports`. **Never** routed to
   `_reject_subtask_for_replan`.
5. **The verdict says why.** The Evaluator marks a verdict whose test is
   listed in `unresolvable_imports` with an explicit, deterministic reason
   (`unresolvable import 'app/…' — generated test imports a module that does
   not exist`), via the #1195 system-reason helper when that has landed, so it
   no longer reads as a generic `flaky`/`consistent_fail`. Verdict category is
   unchanged (the test still fails); only the stated cause becomes accurate.
6. Every rewrite and retry is recorded in `status.json`
   (`import_rewrites`, `import_retries`) so the measured run can count them.

The mapper rule is **not** removed here; it goes in a follow-up once a
measured run shows zero reliance on it.

## Alternatives rejected

- **Reject → Planner replan** (#1192) — reverted by #1194 for cost
  (committed 6→3, 24.7→47.0 min).
- **Retry only, no rewrite** — spends an LLM call on the single most common
  case (`app/` prefix) that a string operation fixes exactly.
- **Rewrite only, no retry** — cannot fix an invented module or an ambiguous
  basename; those would still ship.
- **Rewrite to a root-relative specifier** (`games/tictactoe/game`) — only
  resolves with a mapper or `roots` config; the relative form works in every
  runner, which is what removes the divergence.
- **Fix it in the prompt only** — the prompt already asks for correct imports;
  the model still invents the prefix (specs 193–195). Prompt text is not a
  guarantee; the check is.

## Risks

- **Retry cost:** at most `TFACTORY_GEN_IMPORT_RETRIES` extra sessions per
  affected subtask (default 1), bounded by the existing session timeout.
  Measured in the acceptance run.
- **Wrong rewrite:** prevented by the single-candidate rule; covered by a test
  with two candidate modules.
- **Rewriting source text:** only the quoted specifier token is replaced
  (exact-match on the quoted string), never a regex over code.
- **Step 5 depends on #1195** for the provenance tag; if #1195 has not landed,
  the reason is appended plainly and the tag added when it does.

## Verification

- **Tests first:** relative specifier that is correct from the staged
  location is currently reported unresolvable (confirms the base bug; fails on
  dev); `app/games/tictactoe/game` with one match → rewritten to the relative
  path; two matches → untouched and recorded; a still-bad import triggers
  exactly one retry session (mocked `_invoke_session`) and never calls
  `_reject_subtask_for_replan`; budget 0 → no retry.
- Mutation checks: remove the single-candidate rule → the two-match test
  fails; route the fallback through the replan → the no-replan test fails.
- Suites + ratchet (pinned ruff/mypy) green.
- **Acceptance run on the cluster** (the deciding evidence): the tic-tac-toe
  spec that produced `app/…` (193/194/195 shape) on the deployed build —
  zero shipped tests with unresolvable imports; committed count and duration
  no worse than the #1233 report-only build; rewrites/retries counted from
  `status.json`. Needs a deploy; recorded on #1174.
