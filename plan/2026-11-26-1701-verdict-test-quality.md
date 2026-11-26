---
status: draft
issue: 1701
spec: spec/2026-11-26-1701-verdict-test-quality.md
coders:
  1: codex   # pure selection logic + schema field (owner decision: Codex codes)
  2: codex   # single-test regeneration entrypoint
  3: codex   # evaluator integration: replacement pass, gate, merge, audit
  4: codex   # triager status, envelope and report
reviewers:
  - fable    # owner decision: a Fable agent manages and reviews
---

# Plan: regenerate test-quality rejects inside the evaluator; an all-reject run reports failure

## Approved decisions

The spec is copied here so this plan stands alone. Paths are under `apps/backend/agents/`.

- **Where regeneration runs.** A bounded pass inside `_run_evaluator_session`, after lane
  attribution (~`evaluator.py:3546`) and before `evaluated` and `_advance_to_triager` (~3558-3566).
  The status stays `evaluating`. The same build is used, with no build step and git HEAD asserted
  unchanged, and the work stays inside the verify Job. The Planner replan path is not used.
- **Candidates** come from the final verdicts, rows with `verdict == reject` and a bundle:
  - a test-quality reject: `stability.verdict` is `stable` or `flaky` (the values are lowercase:
    `stable`, `flaky`, `consistent_fail`, `error`);
  - `consistent_fail` with `failure_kind == "assertion"`: only when there are at least 2 vote calls,
    none crashed, and every raw call in `verdicts.vote{i}.json` rejects it with
    `spec_contradiction.criterion` whose normalized quote is a substring of the same AC text
    (`test_plan.json` `AC#N: …` phases, plus the contract `final_acceptance`);
  - never `error`, `not_run`, infra, or unjudged rows.

  The cap is `MAX_REGENERATIONS_PER_RUN = 3`, in stable `test_id` order, with one attempt per test.
- **The replacement** comes from `gen_functional.regenerate_test(task_dir, subtask, brief)`. It
  reuses the existing per-subtask generation session. Its id is `<original>-regen1`, and it never
  touches the plan or the replan caps.
- **It counts only if all of these hold:**
  - the judge verdict is `accept` or `flag`;
  - `stability == stable`;
  - the mutation is not `survived`;
  - on unit lanes the mutation is `killed`; elsewhere `not_run` caps it at `flag`.

  If it counts, the original moves to a new top-level `regenerated` list (`outcome: replaced`).
  Otherwise the original stays `reject` (`outcome: kept_reject`). Any exception leaves the
  originals untouched. The first-pass vote files are renamed `verdicts.pass1.vote{i}.json`.
- **The judge field.** An optional `spec_contradiction.criterion` string of at most 500 characters,
  validated in `evaluator_verdicts._validate_verdicts`, plus one rule in `prompts/evaluator.md`.
- **The triager.**
  - `final_status` is `triaged` when there is any accept, flag or reject, and `triaged_empty`
    only when there are no verdicts.
  - `_status_verdict` returns `("failure", "product_failed: <n> generated tests failed against the
    product")` when accepted + flagged = 0 and rejected > 0.
  - The `triage_report` JSON and Markdown get a "Regenerated tests" section.
- **AIFactory is unchanged.**

## Steps

Steps 1, 2 and 4 touch disjoint files and may run in parallel. Step 3 needs 1 and 2.

1. **Selection and schema.**
   - Add `regeneration.py` with `select_candidates`, `Candidate`, the cap constant and the quote
     normalizer.
   - In `evaluator_verdicts.py`, the optional field.
   - In `prompts/evaluator.md`, the rule.
   - Tests in the new `tests/test_regeneration_select.py`, covering every selection case in the spec.

   Verify: `/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest tests/test_regeneration_select.py tests/test_evaluator_prompts.py tests/test_verdict_vote.py -q`,
   run from the repo root.
2. **`gen_functional.regenerate_test`.**
   - First find the per-subtask generation function the PENDING loop uses (~597-610) and reuse it.
     Add the smallest wrapper that takes a brief and returns one test, or `None`.
   - Tests in the new `tests/test_regenerate_test.py`, with a stubbed session: it returns a single
     test with the `-regen1` id, and the plan, `replan_request.json` and the replan counters are
     untouched.

   Verify: that file plus `tests/test_gen_functional.py`.
3. **Evaluator integration** (`evaluator.py`).
   - The regeneration pass with replacement-only bundles: no build step, HEAD unchanged.
   - The same vote, enrichment and scope.
   - The deterministic replacement gate, the merge into `verdicts` and `regenerated`, and the
     pass-1 audit rename.
   - Fail closed on exceptions.
   - Tests in the new `tests/test_evaluator_regeneration.py`: a replacement is accepted, a kept
     reject, an exception, an unstable replacement, a survived mutation, `not_run` capped at flag,
     and no rebuild.

   Verify: that file plus `tests/test_evaluator.py`, `tests/test_evaluator_integration.py`,
   `tests/test_confidence.py`, `tests/test_reason_provenance.py` and `tests/test_verify_pipeline.py`.
4. **The triager** (`triager.py`, `triage_report.py`).
   - The status rule, `_status_verdict` and the report section.
   - Tests: update the existing expectations in `tests/test_triager.py`,
     `tests/test_triager_empty_warnings.py` and `tests/test_empty_verdict_is_not_done.py` where
     reject-only used to be `triaged_empty`. Add all-reject → `triaged` + `failure`/`product_failed`,
     mixed → success, no verdicts → `triaged_empty`, and the report section.

   Verify: those files plus `tests/test_triage_report*.py`.

**Every step:**

- run ruff check and format --check on the changed files
  (`/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/ruff`);
- run the CI ratchet, with the CI-pinned mypy 1.20.1 and ruff 0.15.17 first on PATH and the repo
  requirements installed:
  `python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts`.
  It must report 0 regressions. It sees only committed files, so the lead commits each step
  before rechecking.

## Tests

The verify commands above, plus the full `tests/` suite once at the end, compared with an
`origin/dev` baseline.

## Rollback

Revert the PR. Rejects become final again, and reject-only runs report `triaged_empty`.
