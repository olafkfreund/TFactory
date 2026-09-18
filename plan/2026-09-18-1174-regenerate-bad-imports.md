---
status: approved
issue: 1174
spec: spec/2026-09-18-1174-regenerate-bad-imports.md
---

# Plan: Stop generated tests shipping imports that resolve to nothing

Approved decisions (self-contained):

- In `agents/gen_functional._generate_one_subtask` (`:911`), after the file
  is read (`:976`) and **before** the source guardrails, run:
  1. detection with relative specifiers resolved against the **staged**
     location `project_dir / files[0]` (today: `spec_dir / files[0]`,
     suspected wrong — confirmed by a failing test first);
  2. **deterministic rewrite**: for each unresolvable specifier, drop leading
     segments until one remainder resolves under `project_dir`; exactly one
     candidate → replace the exact quoted token with the path relative to the
     staged test location (`../../games/tictactoe/game` style); 0 or ≥2
     candidates → leave it;
  3. **bounded retry-with-feedback** while anything is still unresolvable:
     re-run `_invoke_session` for this subtask with a feedback block (bad
     specifier, file, real module path when found); budget
     `TFACTORY_GEN_IMPORT_RETRIES`, default 1; each retry re-reads the file
     and repeats 1–2;
  4. after the budget: record in `status.json` `unresolvable_imports` (today's
     #1233 behaviour) and continue. **Never** `_reject_subtask_for_replan`.
- Record `import_rewrites` and `import_retries` in `status.json`.
- The Evaluator gives a verdict whose test is listed in `unresolvable_imports`
  a deterministic reason (`unresolvable import '<spec>' — generated test
  imports a module that does not exist`) via #1195's `add_system_reason`.
  Verdict category unchanged.
- Python imports stay the pre-flight guard's job; this is JS/TS quoted
  specifiers only (the existing `_IMPORT_RE`).
- The jest-lane `^app/` mapper rule is **not** removed here (follow-up after a
  measured run).
- Depends on #1195 (lands first); this branch rebases onto `dev` after it.

Branch: `fix/1174-regenerate-bad-imports`.

## Steps

0. Rebase onto `origin/dev` once #1195 has merged; confirm
   `confidence.add_system_reason` exists.
   → verify by `git log` showing #1195's merge and the import resolving.
1. **Tests first (must fail on dev)** in `tests/test_unresolvable_import_report.py`
   (extend) or new `tests/test_import_repair.py`, with a tmp project
   (`games/tictactoe/game.js`) and a staged test path `tests/unit/x.test.js`:
   - relative `../../games/tictactoe/game` is **resolvable** from the staged
     location (fails on dev if the base bug is real — **if it passes on dev,
     STOP**: the finding was wrong; update spec + plan and re-ask);
   - `app/games/tictactoe/game` (one match) → file rewritten to
     `../../games/tictactoe/game`, recorded in `import_rewrites`;
   - two candidate modules → untouched, recorded, not rewritten;
   - still unresolvable → exactly one extra `_invoke_session` call (mocked),
     `_reject_subtask_for_replan` never called, `import_retries` recorded;
   - `TFACTORY_GEN_IMPORT_RETRIES=0` → no retry;
   - evaluator: verdict for a listed test gets the system reason, tagged
     `system` in `reasons_source`.
   → verify by failures on unmodified code.
2. `gen_functional.py`: fix the relative base in `_unresolvable_imports`'
   caller (pass the staged path) — no signature change.
   → verify by the relative-specifier test passing and
   `tests/test_unresolvable_import_report.py` still green.
3. `gen_functional.py`: `_rewrite_unambiguous_imports(source, project_dir,
   staged_path) -> (source, rewrites)` — exact quoted-token replacement only.
   → verify by the rewrite + two-candidate tests.
4. `gen_functional.py`: the retry loop in `_generate_one_subtask` with the
   budget env and feedback prompt; the fallback records and continues.
   → verify by the retry / budget-0 / never-replan tests.
5. Evaluator: read `status.json` `unresolvable_imports` and apply the system
   reason to matching verdicts (after `_apply_lane_attribution`, using
   `_resolve_subtask` so paraphrased ids still match).
   → verify by the evaluator test.
6. Mutation checks: remove the single-candidate rule → two-candidate test
   fails; route the fallback through `_reject_subtask_for_replan` →
   never-replan test fails.
   → verify by observed failures, then restore.
7. Full suites + ratchet (pinned, after commit) + format.
   → verify all green.
8. Commit, push, PR to `dev` linking intent/spec/plan.
   → verify CI green; merge per the user's instruction.
9. **Acceptance run on the cluster** (after a deploy): the tic-tac-toe spec
   shape that produced `app/…`; zero shipped tests with unresolvable imports;
   committed count and duration no worse than the #1233 build; rewrites /
   retries counted from `status.json`. Recorded on #1174; close on success.

## Tests

```bash
apps/backend/.venv/bin/pytest tests/test_unresolvable_import_report.py tests/test_import_repair.py tests/test_evaluator.py -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
PATH=<ratchet-venv>/bin:$PATH python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

Revert the commit. Generation returns to report-only (#1233); no stored schema
changes beyond two additive `status.json` counters. Operators can also set
`TFACTORY_GEN_IMPORT_RETRIES=0` to disable retries without a revert (the
rewrite remains).

## Deviations (recorded during implementation, same commit as the code)

- **Step 1 confirmed the suspected base bug** (no STOP): on dev, a correct
  `../../games/tictactoe/game` from the staged test location was recorded as
  unresolvable while the run reported `generated`.
- **Structure (to meet the ratchet's strict bar without `noqa`):** the file
  work (read / rewrite / write / record) is a sync `_import_pass` run via
  `asyncio.to_thread` (ASYNC240); the retry is a `retry(feedback)` callback
  closing over the prompt and verbosity, so `_repair_imports` takes 5
  parameters (PLR0913). `_module_exists` was extracted from
  `_unresolvable_imports` so the rewrite uses the detector's exact resolution
  rules (suffixes, index files).
- The retry prompt is the original prompt plus an `IMPORT FIX REQUIRED`
  block naming the bad specifiers and the test's path. A retry session that
  errors keeps the last file; the caller records what is still bad.
- The evaluator step lives in `_stamp_unresolvable_import_reasons`, called
  from `_apply_lane_attribution` beside the lane/coverage stamps (the same
  post-judge deterministic point), and uses #1195's `add_system_reason`.
- Tests are in a new `tests/test_import_repair.py` (the plan allowed either);
  they drive the real `run_gen_functional` with the SDK faked.
- Mutation checks re-run after the refactor: both still caught.
