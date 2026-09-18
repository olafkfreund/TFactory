---
status: draft
issue: 1259
spec: spec/2026-09-18-1259-lane-progress-live.md
---

# Plan: `lane_progress` must reflect lanes that have run

Approved decisions (self-contained):

- **Derive on read.** One pure function computes lane state from the plan and
  what is on disk; the portal's task-detail route recomputes it on every read.
  The Evaluator's end-of-run write stays, via the same function.
- **Depends on #1258** (separate PR, lands first): this plan reuses its
  `_resolve_subtask`. Before step 1, rebase this branch onto `dev` after
  #1258 merges.
- Rules, per lane that has ≥1 subtask in `test_plan.json`, in precedence:
  1. `error` — the lane has verdicts and every one has
     `signals_summary.stability == "error"` (the #1161 rule, moved);
  2. `executed` — a verdict resolves to the lane, **or** any of its subtasks
     has an artifact: `findings/runs/<id>/`,
     `findings/_run_artifacts/<stem>/` containing a junit or coverage file,
     or `findings/mutants/<id>.*`;
  3. `running` — `status.json` `status == "evaluating"` (lanes only run
     inside the Evaluator) and none of the above;
  4. `pending` — otherwise.
  Lanes absent from the plan keep their existing `status.json` value.
- Verdict → lane via the #1258 resolver, not only `verdict["lane"]`.
- Never raises; unreadable input = "no evidence", never `error`.
- `list_tasks` unchanged (does not return `lane_progress`; per-row stats too
  costly).
- **Frontend (supersedes spec point 3's "no frontend change"; spec point 5 is
  the approved decision):** `LaneStatusGrid.laneCardState` maps `executed` →
  `success`, `error` → `failure` before delegating to `statusColor`. The
  shared `statusColor` is not touched. No new user-facing strings → no i18n.
- New value `running` renders as `in_flight` via the existing fall-through.
- Known limit: on the packed-verify path (#1160) artifacts are not on the
  PVC, so derivation sees nothing there until #1160's consumer lands.

Branch: `fix/1259-lane-progress-live`.

## Steps

1. **Tests first (must FAIL on dev).** New `tests/test_lane_progress.py`:
   - `test_completed_unit_lane_is_executed_before_verdicts` — plan with one
     unit subtask, junit + coverage under `findings/_run_artifacts/<stem>/`,
     no verdicts, status `evaluating` → `unit == "executed"`
   - `test_evaluating_without_artifacts_is_running`
   - `test_not_started_is_pending` (status `generating`)
   - `test_verdict_without_lane_stamp_still_executed`
   - `test_all_stability_error_is_error`
   - `test_lane_not_in_plan_keeps_existing_value`
   - `test_unreadable_plan_does_not_raise`
   → verify by import failing on dev (module absent) — and the first test
   additionally asserted against today's `_derive_lane_progress` returning
   `None`/`pending` for the same fixture, proving the old behaviour is wrong.
2. `apps/backend/agents/lane_progress.py`: implement
   `derive_lane_progress(spec_dir: Path) -> dict[str, str]` per the rules,
   importing `_resolve_subtask` from `agents.evaluator`.
   → verify by step-1 tests passing.
3. `apps/backend/agents/evaluator.py`: `_derive_lane_progress(spec_dir,
   verdicts_path)` becomes a thin call to the new function (keep signature and
   the call at `:2790`), returning `None` only when the plan has no lanes.
   → verify by existing `_derive_lane_progress` tests in
   `tests/test_evaluator.py` passing unchanged.
4. `apps/web-server/server/routes/tfactory_tasks.py::get_task` (`:281`):
   after `status_doc` is read, set
   `status_doc["lane_progress"] = {**status_doc.get("lane_progress", {}),
   **derive_lane_progress(spec_dir)}`, wrapped so a failure keeps the stored
   value.
   → verify by new test in `tests/test_tfactory_routes_tasks.py`: stored
   `pending`, artifacts on disk → response `executed`.
5. `apps/frontend-web/src/components/tfactory/LaneStatusGrid.tsx`
   `laneCardState`: early returns for `executed` → `'success'`, `error` →
   `'failure'`.
   → verify by new cases in `__tests__/LaneStatusGrid.test.tsx`: `executed`,
   `error`, `running`, `pending` → success, failure, in_flight, idle (the
   first two fail before the change).
6. Mutation check: drop the artifact rule from step 2 → the first step-1 test
   fails; restore.
   → verify by observed failure then pass (noted in PR body).
7. Full suites + frontend checks (commands below).
   → verify by all green.
8. Commit `fix(lanes): derive lane_progress from the plan and run artifacts (#1259)`,
   push, PR to `dev` linking intent/spec/plan.
   → verify by CI green.
9. After deploy: watch a real verify in the portal.
   → verify by the lane grid leaving `pending`/idle before the Evaluator
   finishes, and finished lanes showing green; then close #1259.

## Tests

```bash
apps/backend/.venv/bin/pytest tests/test_lane_progress.py tests/test_evaluator.py tests/test_tfactory_routes_tasks.py -q
apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q
cd apps/frontend-web && npx vitest run src/components/tfactory/__tests__/LaneStatusGrid.test.tsx && npm run typecheck
ruff check apps/backend/agents/lane_progress.py apps/web-server/server/routes/tfactory_tasks.py
```

## Rollback

Revert the commit. The route overlay and the frontend mapping are read-side
only; `status.json` keeps the same keys and value vocabulary (plus `running`,
which old frontends already render as in-flight), so no stored data needs
migrating.
