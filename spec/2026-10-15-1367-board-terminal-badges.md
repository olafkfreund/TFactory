---
status: draft
issue: 1367
intent: intent/2026-10-15-1367-board-terminal-badges.md
---

# Spec: `failed` and `generated_empty` get terminal badges on the board

(The intent's title says "looks finished"; the defect is that the card looks
still running. The problem text is correct.)

## Design

All in `apps/frontend-web/src/components/`:

1. `tfactory/TFactoryTaskList.tsx:statusColor` (`:59-74`): add
   `status === 'failed'` to the red branch. This is the shared colour helper;
   it fixes the task list badge, the board's info line (`TFactoryPipelineBoard.tsx:327`)
   and `LaneStatusGrid.laneCardState` (`LaneStatusGrid.tsx:121`) together.
2. `pipeline/TFactoryPipelineBoard.tsx`
   - `isFailed` (`:64-67`): add `'failed'` to the list.
   - `isDone` (`:59-61`): add `'generated_empty'`.
   - `phaseStrip.stateFor` (`:102`): `if (done) return 'done'` becomes
     `if (done) return idx <= reachedIdx ? 'done' : 'pending'`. Every status
     already in `isDone` sits in the Report column (last index), so nothing
     changes for them; a `generated_empty` card (Generate column) shows Execute
     and Report as pending instead of done.
   - `stageOf` phase fallback (`:76-82`): `verify_job_reaped` → `'report'`.
     The reaper only fails verify Jobs (evaluate → triage), so Report is where
     the run died.

The literal `failed` from the orphan reconcile (phase `control_plane_restart`)
keeps falling back to Plan: the summary API (`TFactoryTaskSummary`, `lib/tfactory-api.ts:26-33`)
does not carry `orphaned_from`, and adding it is an API change out of scope. It
still gets the failed badge, which is the reported defect.

## Alternatives rejected

- **Derive `isDone` / `isFailed` from `statusColor`.** Yellow (`*_empty`) would
  also match `evaluated_empty`, which the Triager still follows, so a card
  would show done too early.
- **Show `generated_empty` as failed.** It is the legitimate "plan asked for
  nothing" result (#1253), the same class as `triaged_empty`, which is done.
- **Fix only the board.** `statusColor` has the same gap and the board uses it
  for the card's info line; one more condition fixes all its readers.

## Risks

- `statusColor('failed')` now returns red for lane values too
  (`laneCardState`). A lane reported `failed` is a failure, so this is correct.
- Visual only; no state or API change.

## Verification

- New `pipeline/__tests__/TFactoryPipelineBoard.test.tsx` (vitest, jsdom):
  render the board with a `failed` task and a `generated_empty` task; assert the
  `Failed` and `Triaged` badges (aria-labels) each appear once, and that a
  `failed` / `verify_job_reaped` task renders in the Report column. Fails on
  `origin/dev`, passes after.
- Extend the `statusColor` table in `tfactory/__tests__/TFactoryTaskList.test.tsx`
  with `['failed', 'red']`.
- `npx vitest run` on both files, `npx eslint` and `npx tsc --noEmit` for the
  changed files.
