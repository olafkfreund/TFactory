---
status: approved
issue: 1631
intent: intent/2026-11-19-1631-tf-status-verify-running.md
---

# Spec: Status says running while a verify Job is in flight

## Design

In `dispatch_verify_job` (apps/backend/agents/verify_dispatch.py), right after `_record_spec_worker_ref` and BEFORE `_apply_verify_job`, call a new best-effort `_mark_spec_verifying(spec_dir)`: it patches the control-plane `status.json` to `status="evaluating"`, `phase="verify_job_running"` via `workspace_status.write_status_patch`, only when the current status is `generated`. Writing before the apply removes the race Codex found (no Job exists yet, so no Job copy-back or reaper write can interleave; the check-then-write only races a second dispatch of the same spec, which the durable row already serialises). If the apply fails and dispatch falls back to in-pod, `_unmark_spec_verifying` restores `generated`/`gen_functional_complete` when the phase is still `verify_job_running`, so the in-pod evaluator starts from the same state as today. `evaluating` is in `liveness.ACTIVE_STATUSES`, so `get_task_status` returns `is_running:true` and the MCP proxy follows, with no route change. The #1173 sweep corroborates a quiet `evaluating` spec through `worker_ref.json` (written just before), so it does not false-stall a long Job; if that file is unreadable the 15 min idle deadline applies as for any `evaluating` spec.

## Alternatives rejected

- Query the durable job-state row in the status route: new DB dependency in a hot read path, and the phase would still be stale.
- Add `generated` to ACTIVE_STATUSES: wrongly marks every settled generated spec running.
- Let the Job write status early: its writes land in its restored workspace, not the control plane's copy.

## Risks

The reaper (`_mark_spec_reaped`) and Job copy-back overwrite status afterwards, so no stuck `evaluating`; a dead Job is reaped to `failed` as today.

## Verification

tests/test_verify_dispatch.py: the apply seam observes status `evaluating`/`verify_job_running` at call time (proves write-before-apply); after success it stays so; a terminal status is untouched; a failed apply restores `generated`. A route test shows `is_running` true for that status.json (fails before the fix).
