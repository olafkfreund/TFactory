---
status: approved
issue: 1366
spec: spec/2026-10-15-1366-notify-all-terminal-statuses.md
---

# Plan: every terminal verify status reaches CFactory

## Approved decisions (self-contained)

- Only `triager._notify_completion` sends the CFactory completion event, and it
  is called only from the Triager's writer and the hand-back route. Every run
  that ends before the Triager is never reported.
- Fix in the shared writer `agents/workspace_status.write_status_patch`: notify
  when the written status is in a new `TERMINAL_STATUSES` =
  `{triaged, triaged_empty, triager_failed, failed, planner_failed,
  gen_functional_failed, generated_empty}`. The Triager's writer drops its own
  notify call so nothing is sent twice.
- `evaluator_failed` is NOT in the set: in `kubejob` mode (deployed) the Triager
  still runs after it and notifies. It is notified only on the in-pod path:
  `schedule_evaluator`'s wrapped run, and the crash anchor.
- The reaper, the startup orphan reconcile and the checkout-failure write bypass
  the shared writer; each calls the same `notify_terminal(spec_dir)` helper
  after its write.
- `notify_terminal` never raises. Envelope format unchanged.

## Steps

1. `apps/backend/agents/workspace_status.py`
   - Add `TERMINAL_STATUSES` (frozenset above) and `notify_terminal(spec_dir)`
     to `__all__` and the module. `notify_terminal`: `try:` lazy
     `from agents.triager import _notify_completion  # noqa: PLC0415`, then
     `_notify_completion(spec_dir, read_status(spec_dir))`; `except Exception`
     log at debug and return.
   - `write_status_patch` (`:64-80`): after `emit_stage_event`, add
     `if fields.get("status") in TERMINAL_STATUSES: notify_terminal(spec_dir)`.
   - `anchor_stage_task._on_done` (`:120-130`): inside the `try` after the
     write, `if failed_status not in TERMINAL_STATUSES: notify_terminal(spec_dir)`.
   → `pytest tests/test_workspace_status_anchor.py -q`
   Traps: `stage_events` and `triager` import this module at top level; keep
   both imports lazy. mypy --strict ratchet on changed files.
2. `apps/backend/agents/triager.py:_write_status_patch` (`:418-429`): delete the
   `_notify_completion(spec_dir, status)` line; update the comment above
   `_TERMINAL_STATUSES` (`:374-375`) to say the shared writer notifies.
   → `pytest tests/test_triager_completion_webhook.py tests/test_triager.py -q`
   Traps: keep `_TERMINAL_STATUSES` (verify_pipeline imports it).
3. `apps/backend/agents/evaluator.py:schedule_evaluator` (`:3299-3323`): replace
   `asyncio.create_task(run_evaluator(...))` with a task over a local
   `async def _run_in_pod() -> bool` that awaits `run_evaluator`, then calls
   `notify_terminal(spec_dir)` when `read_status(spec_dir).get("status") ==
   "evaluator_failed"`, and returns the result. Import `notify_terminal` in the
   existing `from agents.workspace_status import (...)` block (`:99`).
   → `pytest tests -q -k evaluator`
   Traps: do not touch `run_evaluator` (verify_pipeline calls it in kubejob).
4. Bypassing writers, each `notify_terminal(Path(spec_dir))` after a successful write:
   - `apps/backend/agents/verify_dispatch.py:_mark_spec_reaped` after
     `path.write_text(...)` (`:1368`).
   - `apps/backend/agents/liveness_sweep.py:reconcile_inline_orphans` after the
     `status_path.write_text` (`:217-220`), before `reconciled.append`.
   - `apps/backend/agents/tools_pkg/tools/task_control.py` after the
     `status.json` write (`:587`), only `if checkout_failed_reason:`.
   → `pytest tests/test_verify_dispatch.py tests/test_liveness_sweep.py tests/test_web_liveness_sweep.py -q`
   Deviation (review): the reaper's guard `_SPEC_TERMINAL_STATUSES`
   (`verify_dispatch.py:1266`) lacked `triager_failed`, `triaged_empty`,
   `planner_failed`, `gen_functional_failed`, so a reap could rewrite an
   already-notified verdict to `failed` and notify twice. It now aliases
   `TERMINAL_STATUSES`. `reap_if_orphaned` calls `_mark_spec_reaped` through
   `asyncio.to_thread`, so the blocking POST stays off the event loop. The
   wiring test covers the guard.
   Traps: lazy-import `notify_terminal` inside the function where the module
   avoids top-level agent imports; follow each file's existing style.
5. `tests/test_terminal_completion_wiring.py` (new): monkeypatch
   `agents.triager._notify_completion` with a recorder; cases:
   each status in `TERMINAL_STATUSES` via `write_status_patch` → one call with
   that status; `evaluator_failed` and `generating` → none; anchor crash with
   `failed_status="evaluator_failed"` → one call; `schedule_evaluator` with a
   stub `run_evaluator` that writes `evaluator_failed` → one call;
   `_mark_spec_reaped` → one call with `failed`; `reconcile_inline_orphans` on a
   `planning` spec → one call with `failed`; `notify_terminal` with a raising
   notifier does not raise.
   → run it against `origin/dev` code (stash steps 1-4) to see it fail, then pass.
   Deviation (implementation): the checkout-failure case is not in this test;
   producing a checkout failure needs the full contract + clone setup of
   `tests/test_spec_ingest_workspace.py`. That path is one guarded call
   covered by review; its existing tests still pass.

6. Review deviations (lead review of the PR), recorded with the code:
   - `apps/backend/agents/liveness.py:mark_stalled`: the watchdog writes
     terminal `failed` (phase `watchdog_failed_inline`) for a stalled
     planning/generating stage straight into `status.json`. It now calls
     `notify_terminal(spec_dir)` after that write. Job-backed stalls stay
     `stalled` and are not notified. The intent's writer table gains this row.
   - `apps/backend/agents/workspace_status.py:notify_terminal`: the send is a
     blocking POST, plus an outbox relay pass. On a thread with a running event
     loop it now goes to the default executor (`loop.run_in_executor`), so an
     unreachable CFactory cannot freeze the API loop. Inline planner and
     gen-functional writes run on it, and so does the anchor's `_on_done`. The
     status is read before the hand-off, so the event carries the status that
     was just written. Without a running loop (the reaper's `to_thread`, the
     startup reconcile thread, sync callers) the send runs inline. In a verify
     Job, `asyncio.run` waits for the default executor on shutdown, so the
     event is not lost when the Job exits.
   - Tests: an inline stall notifies `failed`, a Job-stage stall does not, and
     the notify runs off the loop thread. The async cases wait for the
     executor. The first two cases fail on the previous PR head.

## Tests

```
P=/mnt/code/Source-home/GitHub/TFactory/.venv/bin/python
$P -m pytest tests/test_terminal_completion_wiring.py tests/test_workspace_status_anchor.py \
  tests/test_triager*.py tests/test_completion*.py tests/test_verify_dispatch.py \
  tests/test_liveness*.py tests/test_web_liveness_sweep.py tests/test_web_completion_relay.py -q
ruff check <changed files>; ruff format --check <changed files>
$P -m mypy --strict <changed agent files>   # compare with origin/dev error count
```
All pass; no new mypy errors.

## Rollback

Revert the implementation commit. Behaviour returns to Triager-only
notification; no data or schema change.
