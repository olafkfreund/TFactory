---
status: approved
issue: 1258
author: Olaf Krasicki-Freund
---

# Intent: The judge must see a unit test's measured coverage, not "browser lane"

Follow-up to `intent/2026-09-18-1258-unit-lane-coverage.md` (PR #1296, released
in 0.9.26), which fixed the *recorded* signals. The post-deploy check on the
cluster showed the other half of #1258 is still open.

## Problem

On 0.9.26 (spec `postdeploy-0926-ordinal`, three `(python, pytest, unit)`
subtasks), every stored verdict is now right — `lane: "unit"`, measured
`coverage_new_lines` 7 / 7 / 5 — but the judge's own rationale on the accepts
reads *"coverage N/A on browser lane so not weighed"*. The judge was told the
wrong thing, so it decided accept/flag/reject without coverage — the second
half of #1258's original complaint ("every verdict was formed with that input
pinned").

Why, verified in code:

- The judge's coverage input, `evaluator._coverage_delta_for_subtask`, needs
  both `findings/baseline_coverage.xml` and `findings/runs/<id>/coverage.xml`.
  Nothing ever writes the baseline (its own docstring says so), so it returns
  `None` for every test — even when the lane's coverage report exists (the
  post-judge `_measured_coverage` finds it via `runs/` or `_run_artifacts/`).
- `prompts_pkg/prompts.py:1533-1541` renders any `None` as
  `"coverage: N/A (browser lane)"` — for a framework that skips coverage *and*
  for "not measured" alike. So every unit test is described to the judge as a
  browser-lane test.

## Proposed outcome

- For a unit (or any coverage-producing) lane, the judge is given the coverage
  that was actually measured for that test (covered SUT lines), from the same
  source the post-judge stamp uses.
- "Browser lane / coverage not applicable" is said only for a framework whose
  coverage strategy is `skip`; "not measured" is said as such, and never
  labelled as browser lane.
- The judge's rationale on a unit test stops claiming it is a browser test, and
  coverage becomes one of the inputs its decision can weigh — observed on a
  real cluster run.

## Affected users and systems

- `apps/backend/agents/evaluator.py` (`_coverage_delta_for_subtask`,
  `_measured_coverage`, bundle assembly), `prompts_pkg/prompts.py`
  (`_format_evaluator_per_test_block`), `prompts/evaluator.md` (how the judge
  is told to use coverage)
- Every verdict's accept/flag/reject on coverage-producing lanes; the
  confidence score and triage ranking downstream
- The deployed cluster (verification)

## Constraints

- Must not produce spurious rejects: the original reason for the "N/A" label
  was to stop the judge reading a missing value as "0% coverage". A test that
  covers SUT lines must not be penalised because no baseline exists.
- `coverage_delta_pct` stays `None` without a baseline (no fabricated deltas).
- Browser / skip-strategy lanes keep today's behaviour.
- Deterministic signals over judge prose: the prompt must state what was
  measured, not ask the judge to infer it.

## Open questions

1. What exactly does the judge get without a baseline — the covered-SUT-lines
   count only (recommended: it is real and needs no baseline), or also a
   "new lines" set? The evaluator prompt's coverage rule currently keys on
   `delta_pct`; the spec must adjust that rule so `delta_pct` absent ≠ 0.
2. Write a baseline snapshot so a real delta exists (a coverage run of the SUT
   *before* the generated tests)? Out of scope recommended — it is an extra
   lane run per spec; the covered-lines count answers "does this test exercise
   the subject" now.
