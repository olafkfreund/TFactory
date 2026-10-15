---
status: approved
issue: 1366
intent: intent/2026-10-15-1366-notify-all-terminal-statuses.md
---

# Spec: every terminal verify status reaches CFactory

## Design

Move the "status went terminal → notify" rule from the Triager into the shared
status writer, and call the same helper from the three writers that bypass it.

1. `apps/backend/agents/workspace_status.py`
   - Add `TERMINAL_STATUSES`: the statuses after which no stage runs:
     `triaged`, `triaged_empty`, `triager_failed`, `failed`, `planner_failed`,
     `gen_functional_failed`, `generated_empty`.
     `evaluator_failed` is **not** in it: in `kubejob` mode
     `verify_pipeline.run_verify` (`verify_pipeline.py:88-97`) still runs the
     Triager after an evaluator failure, and the Triager sends the event.
   - Add `notify_terminal(spec_dir)`: reads `status.json`, lazily imports
     `agents.triager._notify_completion` and calls it. Swallows every
     exception (the envelope builder can itself raise, `triager.py:1024`).
   - `write_status_patch`: after the stage event, call `notify_terminal` when
     `fields["status"]` is in `TERMINAL_STATUSES`.
   - `anchor_stage_task._on_done`: after the crash write, call
     `notify_terminal` when `failed_status` is not in `TERMINAL_STATUSES`
     (the write already notified otherwise). Anchors exist only on the
     in-pod schedulers (`evaluator.py:3317`, `triager.py:1914`), where nothing
     runs after a crash, so an `evaluator_failed` crash is terminal there.
2. `apps/backend/agents/triager.py:_write_status_patch` (`:418-429`): drop its
   own `_notify_completion` call; the shared writer now does it. Hand-back and
   Backstage callbacks stay. `_TERMINAL_STATUSES` stays (it gates them, and
   `verify_pipeline.py:107-115` reads it).
3. `apps/backend/agents/evaluator.py:schedule_evaluator` (`:3299-3323`): the
   in-pod path. Wrap `run_evaluator` so that when it returns with status
   `evaluator_failed`, `notify_terminal` is called. `verify_pipeline` calls
   `run_evaluator` directly and is untouched, so `kubejob` sends no early event.
4. Writers that bypass `write_status_patch` call `notify_terminal` after their
   write succeeds:
   - `agents/verify_dispatch.py:_mark_spec_reaped` (`:1352-1376`),
   - `agents/liveness_sweep.py:reconcile_inline_orphans` (`:180-222`),
   - `agents/tools_pkg/tools/task_control.py` checkout failure (`:563-587`),
     only when `checkout_failed_reason` is set.

The envelope is unchanged: `_outcome_for_status` (`triager.py`) already maps
every status other than `triaged` / `triaged_empty` to `failure`.

## Alternatives rejected

- **Notify from each stage's failure branches.** About twenty call sites, and
  the next new failure branch would forget. The shared writer is the one place
  every agent stage already goes through.
- **Add `evaluator_failed` to `TERMINAL_STATUSES`.** In deployed `kubejob` mode
  CFactory would get a `failure` event and then the Triager's event for the
  same run. Two terminal events for one run is a new bug.
- **Rewrite the reaper / sweep / checkout to use `write_status_patch`.** It
  would also emit stage events and change `updated_at` handling in code that
  is fine; a one-line notify call is the smaller change.
- **Have CFactory infer failure from staleness.** A reader-side guess for a
  writer-side gap; CFactory is out of scope.

## Risks

- **Duplicate events.** A run already notified that is later rewritten to a
  terminal status (for example a Job that wrote `triager_failed` and then exits
  non-zero is reaped to `failed`, because `_SPEC_TERMINAL_STATUSES`,
  `verify_dispatch.py:1266`, lacks `triager_failed`) sends a second event.
  Both are failures. The outbox keys idempotency per event, so CFactory sees
  the latest. Not widened here.
- **Blocking in the web-server loop.** The reaper and the startup reconcile now
  call a synchronous POST (default 5 s timeout,
  `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT`). The hand-back route already does the
  same. With the outbox enabled the POST is the relay pass.
- **Import cost.** The lazy `agents.triager` import now happens in planner and
  gen-functional processes on a terminal write only.
- No-op unless a completion webhook URL is configured (`_deliver_completion`).

## Verification

- New `tests/test_terminal_completion_wiring.py`: for each writer —
  `write_status_patch` with each status in `TERMINAL_STATUSES`, the anchor crash
  with `evaluator_failed`, `schedule_evaluator`'s failure path,
  `_mark_spec_reaped`, `reconcile_inline_orphans`, checkout failure — assert
  `_notify_completion` is called once with that status; and that
  `write_status_patch(status="evaluator_failed")` and a non-terminal status do
  not notify. Fails on `origin/dev`, passes after.
- Existing suites still pass: `tests/test_triager*.py`, `tests/test_liveness*.py`,
  `tests/test_verify_dispatch*.py`, `tests/test_workspace_status*.py`,
  `tests/test_completion*.py`.
- `ruff check`, `ruff format --check`, and mypy if configured for these files.
