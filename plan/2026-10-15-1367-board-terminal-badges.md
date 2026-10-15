---
status: draft
issue: 1367
spec: spec/2026-10-15-1367-board-terminal-badges.md
---

# Plan: `failed` and `generated_empty` get terminal badges on the board

## Approved decisions (self-contained)

- The pipeline board's `isFailed` misses the literal `failed` (planner, reaper,
  orphan reconcile, checkout) and `isDone` misses `generated_empty`, so those
  cards show no terminal badge and an `active` chip. The shared `statusColor`
  also colours `failed` blue (in-flight).
- `failed` → failed badge and red; `generated_empty` → done badge (like
  `triaged_empty`), with the never-run stages shown pending, not done.
- A reaped run (phase `verify_job_reaped`) goes to the Report column.
- Orphan-reconciled `failed` (phase `control_plane_restart`) stays in Plan: the
  summary API has no `orphaned_from`; out of scope.
- Do not derive the board predicates from `statusColor` (yellow would make
  `evaluated_empty` done too early).

## Steps

1. `apps/frontend-web/src/components/tfactory/TFactoryTaskList.tsx:62-69`: add
   `status === 'failed' ||` to the red condition. Add `['failed', 'red']` to
   the `statusColor` `it.each` table in
   `tfactory/__tests__/TFactoryTaskList.test.tsx:61-78`.
   → `npx vitest run src/components/tfactory/__tests__/TFactoryTaskList.test.tsx`
   Traps: `LaneStatusGrid.laneCardState` also reads it; `failed` → failure is intended.
2. `apps/frontend-web/src/components/pipeline/TFactoryPipelineBoard.tsx`:
   - `:60` add `'generated_empty'` to the `isDone` list;
   - `:66` add `'failed'` to the `isFailed` list;
   - `:78` (phase fallback) add `if (p === 'verify_job_reaped') return 'report';`
     before the `triager`/`evaluator` check;
   - `:102` `if (done) return 'done';` → `if (done) return idx <= reachedIdx ? 'done' : 'pending';`
   → step 3's test.
   Traps: `idx` is declared on the line above; keep the order of checks
   (`act === stage` first).
3. New `apps/frontend-web/src/components/pipeline/__tests__/TFactoryPipelineBoard.test.tsx`
   (`@vitest-environment jsdom`, same imports as `TFactoryTaskList.test.tsx`):
   render `<TFactoryPipelineBoard tasks={[...]} onSelectTask={() => {}} />` with
   - a `failed` task (phase `verify_job_reaped`): `getAllByLabelText('Failed')`
     has length 1, and the card sits in the Report column;
   - a `generated_empty` task: `getAllByLabelText('Triaged')` has length 1, and
     its Execute / Report chips have `data-state="pending"`.
   Stub `ResizeObserver` if jsdom lacks it. Run it with step 2 stashed to see
   it fail, then pass.
   → `npx vitest run src/components/pipeline`

## Tests

From `apps/frontend-web`:
```
npx vitest run src/components/pipeline src/components/tfactory
npx eslint src/components/pipeline src/components/tfactory/TFactoryTaskList.tsx
npx tsc --noEmit -p .
```
All pass, no new lint or type errors.

## Rollback

Revert the commit; display-only change.
