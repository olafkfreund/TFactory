---
status: approved
issue: 1258
spec: spec/2026-09-18-1258-unit-lane-coverage.md
---

# Plan: Unit-lane coverage must reach the verdict

Approved decisions (self-contained):

- Separate PR from #1259; this one lands first (#1259 reuses the resolver).
- Root-cause hypotheses (to be confirmed by step 1 before any fix):
  1. `_stamp_verdict_lanes` (`evaluator.py:751`) matches the judge-authored
     `verdict["test_id"]` exactly against plan subtask ids; nothing validates
     that id, so a paraphrased id leaves every verdict unattributed.
  2. `_measured_coverage` (`:806`) reads only
     `findings/runs/<test_id>/coverage.xml`; the Nix batched path never writes
     it, while the host runner persists to
     `findings/_run_artifacts/<test-file-stem>/coverage.xml` (`:1524`), which
     nothing reads for the verdict.
- One resolver, `_resolve_subtask(plan, verdict)`: exact `test_id` → else
  `test_file` vs `files_to_create[0]` (posix, leading `./` stripped, full
  relative path) → else basename, **only if unique in the plan** → else
  `None`. Never default to unit (#1018).
- On a file/basename match, rewrite `verdict["test_id"]` to the plan id.
- `_measured_coverage` takes the resolved subtask; lookup order
  `runs/<id>/coverage.xml`, then `_run_artifacts/<stem>/coverage.xml`.
  Unresolved or none → `(None, None)`.
- `_stamp_verdict_coverage` overwrites the judge's coverage keys on every
  verdict, creating `signals_summary` if missing, so a judge `0` never
  survives. Unmeasured is `None`, never `0`.
- No change to `_capturing_coverage`, the Nix batched path, or the prompt.
- `coverage_delta_pct` stays `None` without a baseline file (existing,
  deliberate); acceptance is on `coverage_new_lines`, plus `delta_pct` when a
  baseline is present.

Branch: `fix/1258-unit-lane-coverage` (off `origin/dev`). All code in
`apps/backend/agents/evaluator.py`; tests in `tests/test_evaluator.py`.

## Steps

1. **Reproduce (tests first, must FAIL on dev).** In `tests/test_evaluator.py`
   add fixtures building a spec dir: `test_plan.json` with one completed
   `(python, pytest, unit)` subtask `id="ac1-unit"`,
   `files_to_create=["tests/test_ac1.py"]`; a `verdicts.json` with
   `test_id="test_ac1"`, `test_file="tests/test_ac1.py"`,
   `signals_summary={"coverage_delta_pct": 0, "coverage_new_lines": 0}`; and a
   Cobertura `coverage.xml` at `findings/_run_artifacts/test_ac1/` covering 5
   lines of `src/app.py` plus lines of `tests/test_ac1.py`. Tests:
   - `test_lane_stamped_when_judge_test_id_differs` → `lane == "unit"`,
     `test_id == "ac1-unit"`
   - `test_coverage_read_from_run_artifacts` → `coverage_new_lines == 5`
   - `test_unmeasured_coverage_is_none_not_zero` (no coverage file) →
     both keys `None`
   - `test_delta_pct_nonzero_with_baseline` (baseline covering 2 lines) →
     `coverage_delta_pct > 0`
   - `test_ambiguous_basename_stays_unattributed` (two subtasks
     `a/test_x.py`, `b/test_x.py`; verdict `test_file="test_x.py"`) → no
     `lane`
   → verify by running them on unmodified dev: the first two and the
   delta test FAIL; ambiguity + None tests may pass (they guard the fix).
   **If the failures do not match the hypotheses** (e.g. lane stamps fine and
   coverage is the only gap, or `0` comes from somewhere else) — STOP, update
   this plan and the spec's findings, and re-ask before step 2.
2. `evaluator.py`: add `_resolve_subtask(plan, verdict) -> dict | None` next
   to `_lane_by_test_id` (`:730`), with a `_norm_rel(path)` helper (posix,
   strip `./`). Build the id map and basename counts once per call.
   → verify by a direct unit test of the resolver's four outcomes.
3. `evaluator.py`: `_stamp_verdict_lanes` uses the resolver; on match set
   `lane` and, when matched by file, rewrite `test_id`. Unmatched count and
   warning unchanged.
   → verify by step-1 lane test passing; existing #1018 tests in
   `tests/test_evaluator.py` still pass.
4. `evaluator.py`: `_measured_coverage(spec_dir, subtask)` (signature change;
   one caller) with the two-path lookup; `_stamp_verdict_coverage` loads the
   plan once, resolves each verdict, creates `signals_summary` when absent,
   always writes both keys.
   → verify by step-1 coverage, None and delta tests passing.
5. Mutation check: temporarily revert step 2's file/basename branches →
   lane + coverage tests fail; restore.
   → verify by observed failure then pass (recorded in the PR body).
6. Full suites.
   → verify by `apps/backend/.venv/bin/pytest tests/ -m "not slow" -q` and
   `apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q`
   green (venv refreshed with both requirement sets first).
7. Commit `fix(evaluator): resolve verdicts to plan subtasks so unit coverage reaches the verdict (#1258)`,
   push, PR to `dev` linking intent/spec/plan, test output + mutation note in
   body.
   → verify by CI green.
8. After deploy: one real unit-lane verify on
   https://tfactory.freundcloud.org.uk.
   → verify by `findings/verdicts.json` showing `lane: "unit"` and non-null
   `coverage_new_lines` on the unit verdicts; then close #1258.

## Tests

```bash
apps/backend/.venv/bin/pytest tests/test_evaluator.py -q -k "lane or coverage or basename"
apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q
ruff check apps/backend/agents/evaluator.py && ruff format --check apps/backend/agents/evaluator.py
```

Expected: step-1 tests fail on dev before step 2, all pass after; full suites
green.

## Rollback

Revert the commit. The change is confined to post-judge stamping in
`evaluator.py`; no stored schema, status field or on-disk layout changes, so
existing workspaces read the same before and after.

## Deviations (recorded during implementation, same commit as the code)

- **Step 4 signature kept.** `_measured_coverage(spec_dir, test_id, stem=None)`
  instead of `(spec_dir, subtask)`: existing #1024 tests call it with a plain
  id and run `_stamp_verdict_coverage` with no `test_plan.json`; the
  subtask-only signature would have broken both. The stamp resolves the
  subtask, then passes id + stem; with no plan it falls back to the verdict's
  own `test_id`/`test_file`. Same outcome, smaller change.
- **Fixture:** reuses the file's existing `_COBERTURA` (2 covered SUT lines)
  rather than a new 5-line report.
- **Step 1 confirmed both hypotheses**, and pinned the unexplained `0`: a
  verdict without `signals_summary` was skipped by the coverage stamp, so the
  judge's value survived. Covered by
  `test_missing_signals_summary_cannot_keep_a_judge_zero`.
- **Step 5 needed two mutations, not one.** Removing the resolver's file
  fallback fails the lane tests but not the coverage test (the coverage stamp
  independently derives the stem from `verdict["test_file"]`), so the
  `_run_artifacts` lookup got its own mutation — which fails the 3 coverage
  tests. Both observed, both restored.
- `_lane_by_test_id` deleted: its only caller now uses `_resolve_subtask`.
