---
status: approved
issue: 1258
spec: spec/2026-09-18-1258-judge-coverage-input.md
---

# Plan: The judge must see a unit test's measured coverage, not "browser lane"

Approved decisions (self-contained):

- `EvaluatorSignals` gains `coverage_covered_lines: int | None = None` and
  `coverage_na_reason: str | None = None`.
- Each of the **five** bundle builders states its own case:
  `_assemble_signals` — skip-strategy framework → `"browser lane"`, otherwise
  `coverage_covered_lines = _measured_coverage(spec_dir, subtask["id"], stem)`
  (stem of `files_to_create[0]`; may be `None`);
  `_build_browser_signal_bundle` → `"browser lane"`;
  `_build_api_signal_bundle` → `"api lane — no line coverage"`;
  `_build_jest_signal_bundle`, `_build_go_signal_bundle` →
  `"coverage not wired for this lane"`.
  `coverage_delta` unchanged (baseline-only).
- Rendering in `prompts_pkg/prompts.py::_format_evaluator_per_test_block`,
  first match wins: delta → today's numeric line; covered count →
  `coverage: covered_sut_lines=N (no baseline, so no delta — total subject
  lines this test executed, not new lines)`; reason → `coverage: N/A
  (<reason>)`; else → `coverage: not measured (no coverage report for this
  run)`.
- `prompts/evaluator.md`: any `coverage: N/A (…)` → skip the coverage rule
  (keep the browser-lane wording as the example); `not measured` → don't factor
  coverage, never 0; `covered_sut_lines > 0` → evidence, don't apply the
  `new_lines=0` rule; `covered_sut_lines = 0` → **flag**. Matrix rows added;
  the `new_lines` row stays for baselines.
- Post-judge stamp unchanged. No baseline snapshot.

Branch: `fix/1258-judge-coverage-input` (off `origin/dev`).

## Steps

1. **Tests first (must fail on dev)** in `tests/test_evaluator_prompts.py`
   (+ `tests/test_evaluator.py` for bundles):
   - render: covered count → the `covered_sut_lines=N` line and **no**
     "browser lane"; reason `"api lane — no line coverage"` → `coverage: N/A
     (api lane — no line coverage)`; neither → `coverage: not measured …`;
     delta present → numeric line unchanged;
   - bundle: a pytest subtask with a Cobertura report at
     `findings/_run_artifacts/<stem>/coverage.xml` → `_assemble_signals`
     yields `coverage_covered_lines` = the measured count; a skip-strategy
     (Playwright) subtask → `coverage_na_reason="browser lane"`;
   - each of the browser/api/jest/go builders sets its reason;
   - `evaluator.md` contains the `covered_sut_lines` and `not measured` rules
     and still the browser-lane N/A example.
   → verify they fail on dev.
2. `evaluator.py`: add the two fields to `EvaluatorSignals`; fill them in
   `_assemble_signals` and the four other builders.
   → verify the bundle tests pass.
3. `prompts_pkg/prompts.py`: the four-way rendering.
   → verify the render tests pass.
4. Update the existing pins in `tests/test_evaluator_prompts.py`
   (`:230`, `:348`, `:451` build `coverage_delta=None` bundles expecting "N/A
   (browser lane)"): set `coverage_na_reason="browser lane"` so they keep
   testing the browser case explicitly.
   → verify `tests/test_evaluator_prompts.py`, `test_prompt_contract.py`,
   `test_evaluator.py` green.
5. `prompts/evaluator.md`: the coverage section + matrix rows.
   → verify the prompt tests (`:373`, `:384` and the new ones) pass.
6. **Mutation checks:** render every `None` as "browser lane" again → the
   api/not-measured render tests fail; drop the `_measured_coverage` call →
   the pytest-bundle test fails.
   → verify both, then restore.
7. Full suites + ratchet (pinned, on the commit) + format.
8. Commit, push, PR to `dev` linking intent/spec/plan; merge when CI green.
9. **Cluster (after the next release):** re-run the `ordinal` spec shape; the
   judge's rationale on unit tests no longer says "browser lane" and refers to
   the covered lines; record on #1258 and close it.

## Tests

```bash
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/test_evaluator_prompts.py tests/test_prompt_contract.py tests/test_evaluator.py -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q
PATH=<ratchet-venv>/bin:$PATH python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

Revert the commit. The change is confined to the judge's input and prompt; the
stored verdict fields (#1296) are untouched, so reverting only restores the
old "browser lane" wording and coverage-blind judging.
