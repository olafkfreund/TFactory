---
status: approved
issue: 1259
intent: intent/2026-09-18-1259-lane-progress-live.md
---

# Spec: `lane_progress` must reflect lanes that have run

Decisions carried from intent review: derive on read (cannot go stale);
separate PR from #1258, landing after it.

## Design

One pure function computes lane state from the plan and what is on disk.
The Evaluator keeps writing its end-of-run value (via the same function), and
the portal recomputes on every read, so a mid-run read is live.

1. **`agents/lane_progress.py`** (new, one function):
   `derive_lane_progress(spec_dir: Path) -> dict[str, str]`
   - **Requested lanes** come from `test_plan.json`: every lane that has at
     least one subtask. Lanes with no subtask are left as whatever
     `status.json` already has (`pending` from the initialisers) — "nobody
     asked" stays distinct.
   - Per requested lane, in precedence order:
     - `error` — every verdict for that lane has `stability == "error"`
       (today's #1161 rule, unchanged, moved here);
     - `executed` — a verdict for that lane exists, **or** any subtask of that
       lane has a persisted run artifact:
       `findings/runs/<id>/`, `findings/_run_artifacts/<stem>/` (junit or
       coverage file), or `findings/mutants/<id>.*`;
     - `running` — status is in an execution phase (Evaluator started) and no
       artifact yet;
     - `pending` — otherwise.
   - Verdict lanes are resolved via the plan (the #1258 resolver), not only
     `verdict["lane"]`, so a missing stamp no longer leaves the lane
     `pending` (intent point 2).
   - Never raises; unreadable files degrade to "no evidence", not to `error`.
2. **`evaluator.py`** — `_derive_lane_progress` delegates to the new function
   (keeps its call site at `:2790` and the `status.json` write).
3. **`apps/web-server/server/routes/tfactory_tasks.py::get_task`** (`:281`) —
   after reading `status.json`, overlay `lane_progress` with
   `derive_lane_progress(spec_dir)`. The portal's `LaneStatusGrid` and
   `TFactoryTaskDetail` already read `status_json.lane_progress`; no frontend
   change.
4. **Vocabulary:** adds one value, `running`.
5. **Frontend — existing bug found while specifying:**
   `LaneStatusGrid.laneCardState` buckets through `statusColor`
   (`TFactoryTaskList.tsx`), which knows task statuses, not lane values.
   `executed` and `error` fall through to `blue` → both render as
   **in flight**. So since #1161 a finished lane and a broken lane have both
   looked "running" in the portal. Fix in `laneCardState` only (not in the
   shared `statusColor`, which the task list uses for task statuses):
   `executed` → `success`, `error` → `failure`, before delegating to
   `statusColor`. `running` already maps to `in_flight` via the fall-through,
   and `pending` to `idle` — no change. No new user-facing strings, so no
   i18n keys.

The list endpoint (`list_tasks`, `:254`) is not changed — it does not return
`lane_progress`, and deriving for every row would stat every workspace on each
list call.

## Alternatives rejected

- **Write `lane_progress` as each lane finishes** (Executor / runner seam) —
  five execution paths through `_build_all_bundles`, one writer each; #1161
  already rejected per-call-site stamping for exactly this drift.
- **Remove the field** — the issue allows it, but the portal's lane grid is
  built on it and the data to make it correct is already on disk.
- **Derive only from verdicts** (today) — verdicts exist only after the judge,
  so every mid-run read is `pending`. That is the bug.

## Risks

- **Read cost:** `get_task` gains a plan read plus a few `stat`/`glob` calls
  in one spec dir — negligible for a single-task detail view.
- **`running` misread:** a crashed Evaluator leaves a lane `running` forever.
  Mitigation: `running` only while `status` is an execution status; once the
  task is terminal (`evaluated`, `triaged*`, `*_failed`) a lane with no
  artifact reads `pending`/`error` per the rules above.
- **Packed verify (#1160):** on the packed path, artifacts land in the object
  store, not the PVC, so derivation on the PVC sees nothing until #1160's
  control-plane consumer lands. Same limitation every PVC reader has; noted in
  #1160, not solved here.
- **Frontend:** `laneCardState` change is local to the lane grid; the task
  list's `statusColor` is untouched.

## Verification

- **Mutation check (from the issue):** fixture spec with a completed unit
  lane — junit + coverage under `_run_artifacts/<stem>/`, no verdicts yet —
  `derive_lane_progress` must return `unit: executed`. Fails on dev (all
  `pending`); reverting the artifact rule makes it fail again.
- Verdicts present but none carries `lane` → lane still `executed` (intent
  point 2).
- All verdicts `stability: error` → `error`; a lane not in the plan → keeps
  its existing value.
- `get_task` route test: status.json says `pending`, artifacts on disk →
  response says `executed`.
- Frontend: `LaneStatusGrid` test — `executed` → success, `error` →
  failure, `running` → in_flight, `pending` → idle (the first two fail on
  dev); `npm run typecheck`.
- Full backend + web-server suites green.
- Cluster: during a real verify, the portal lane grid moves off `pending`
  before the Evaluator finishes.
