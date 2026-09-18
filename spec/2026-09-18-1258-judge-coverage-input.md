---
status: draft
issue: 1258
intent: intent/2026-09-18-1258-judge-coverage-input.md
---

# Spec: The judge must see a unit test's measured coverage, not "browser lane"

Decisions carried from intent review: the judge gets the **covered-SUT-lines
count** (no baseline needed); the prompt's coverage rule is adjusted so an
absent delta is not read as zero; **no baseline snapshot** in this change.

## Findings (dev, after #1296)

- The judge's per-test block is built from `EvaluatorSignals.coverage_delta`,
  set by `_coverage_delta_for_subtask` (`evaluator.py:~1156`). It returns
  `None` when the framework's `coverage_strategy == "skip"` **or** when either
  `findings/baseline_coverage.xml` or `findings/runs/<id>/coverage.xml` is
  missing — and the baseline is never written, so it is `None` for every test.
- `prompts_pkg/prompts.py:1533-1541` renders every `None` as
  `coverage: N/A (browser lane)`; its own comment says it covers both cases.
- `_measured_coverage(spec_dir, test_id, stem)` (from #1296) already returns
  the covered-SUT-lines count from the lane's own report (`runs/<id>/` or
  `_run_artifacts/<stem>/`), test files excluded — the post-judge stamp uses
  it. The lane runs (stability/mutation) happen before the bundle is assembled,
  so the report exists when the judge's block is built. On the 0.9.26 cluster
  run it produced 7 / 7 / 5.
- `prompts/evaluator.md` has one numeric rule, built on the baseline delta:
  "`new_lines=0` is a reject signal", and a matrix row
  `coverage_delta.new_lines | 0 | reject`. `new_lines` there means lines newly
  covered **relative to a baseline** — not the same quantity as a test's total
  covered lines.

## Design

1. **Two new facts on the bundle.** `EvaluatorSignals` gains
   `coverage_covered_lines: int | None = None` and
   `coverage_na_reason: str | None = None` — *why* coverage does not apply, set
   by the code that knows the lane. **Five** builders construct bundles, so each
   states its own case:
   - `_assemble_signals` (pytest and other runner-driven lanes): framework
     `coverage_strategy == "skip"` → `coverage_na_reason="browser lane"`;
     otherwise `coverage_covered_lines = _measured_coverage(spec_dir,
     subtask["id"], stem)` (may be `None` = not measured);
   - `_build_browser_signal_bundle` → `"browser lane"`;
   - `_build_api_signal_bundle` → `"api lane — no line coverage"`;
   - `_build_jest_signal_bundle`, `_build_go_signal_bundle` → `"coverage not
     wired for this lane"` (true today; a later change can measure them).
   `coverage_delta` is unchanged (set only when a baseline exists).
2. **Rendering** in `_format_evaluator_per_test_block`, in order:
   - baseline delta present → today's numeric line (unchanged);
   - `coverage_covered_lines` set → `coverage: covered_sut_lines=N (no
     baseline, so no delta — total subject lines this test executed, not new
     lines)`;
   - `coverage_na_reason` set → `coverage: N/A (<reason>)`;
   - otherwise → `coverage: not measured (no coverage report for this run)`.
   "browser lane" appears only where a builder said so.
3. **Prompt rules** (`prompts/evaluator.md`, coverage section + matrix):
   - any `coverage: N/A (…)` → skip the coverage rule (generalises today's
     "N/A (browser lane)" rule);
   - `not measured` → do not factor coverage; never treat it as 0;
   - `covered_sut_lines > 0` → evidence the test exercises the subject; do not
     apply the `new_lines=0` rule (no baseline); weigh with the other signals;
   - `covered_sut_lines = 0` → the test executed none of the subject's code →
     **flag** (not reject: weaker than a baseline `new_lines=0`).
   The matrix gains these rows; the `new_lines` row stays for when a baseline
   exists.
4. The post-judge stamp (`_stamp_verdict_coverage`) is unchanged — the judge's
   input and the stored value now come from the same `_measured_coverage`.

## Alternatives rejected

- **Reuse the `new_lines` field for the covered count** — the judge would apply
  "`new_lines=0` → reject" to a different quantity; misleading by construction.
- **Write a baseline snapshot** — an extra coverage run per spec; out of scope
  at intent review.
- **Just rename "browser lane" to "not measured"** — stops the false claim but
  still withholds a signal that exists.

## Risks

- **Verdict shift:** unit tests that previously had coverage ignored will now
  have it considered; a test covering 0 subject lines moves accept → flag. This
  is the intended correction; measured on a real run before/after.
- **Prompt drift:** `tests/test_evaluator.py` / prompt-contract tests
  (#1250) that pin the rendered block or prompt text must be updated in the
  same PR. A bundle built with neither new field now renders "not measured"
  instead of "N/A (browser lane)" — any test relying on the old default is
  updated to set the reason explicitly.
- **Lane wording:** api/jest/go bundles stop being called "browser lane"; the
  generalised "any N/A → skip" rule keeps their verdict behaviour unchanged.
- **Order:** relies on lane runs finishing before `_assemble_signals`; asserted
  by a test that builds a bundle after a (faked) lane run wrote the report.

## Verification

- Unit: `_format_evaluator_per_test_block` for all four renderings; "browser
  lane" appears only when a builder set it (mutation: collapsing the cases back
  to one fails it).
- Bundles: a pytest subtask with a report under `_run_artifacts/<stem>/` gets
  `coverage_covered_lines` = the measured count; each of the browser, api,
  jest and go builders sets its `coverage_na_reason`.
- Prompt: `evaluator.md` states the three rules; any prompt-contract test
  updated.
- Suites + ratchet green.
- **Cluster (after deploy):** re-run the `ordinal` spec shape; the judge's
  rationale on unit tests no longer says "browser lane", references the
  covered lines, and verdicts are recorded. Then close #1258.
