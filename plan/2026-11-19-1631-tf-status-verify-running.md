---
status: draft
issue: 1631
spec: spec/2026-11-19-1631-tf-status-verify-running.md
coders:
  2: codex   # contained edit plus a seam test
reviewers:
  - codex
---

# Plan: Status says running while a verify Job is in flight

Approved spec decisions: write `status=evaluating`, `phase=verify_job_running` to the control-plane status.json BEFORE the Job is applied, only from `generated`; restore `generated`/`gen_functional_complete` if the apply fails and dispatch falls back in-pod. No route change (`evaluating` is already an ACTIVE status).

## Steps

2. apps/backend/agents/verify_dispatch.py (`dispatch_verify_job`, after `_record_spec_worker_ref(spec_dir, worker_ref)`, before `_apply_verify_job`; restore inside the `except` that returns None): add `_mark_spec_verifying(spec_dir)` and `_unmark_spec_verifying(spec_dir)` using `agents.workspace_status.read_status/write_status_patch`, best-effort (OSError logged, never raised). Tests in tests/test_verify_dispatch.py with a spec_dir under `tmp_path` holding status.json: (a) the `apply_fn` seam reads status.json at call time and sees evaluating/verify_job_running; (b) after success it stays; (c) a terminal status (`triaged`) is untouched; (d) a failing apply restores generated/gen_functional_complete. Add a route test in the existing execution status test file showing `is_running` true for status=evaluating phase=verify_job_running. → verify: `pytest tests/test_verify_dispatch.py -q` (fails without the change).
   Traps: existing dispatch tests pass nonexistent spec_dir paths: the helpers must swallow the missing-file case.

## Tests

`pytest tests/test_verify_dispatch.py tests/test_liveness*.py -q`, execution route tests, ratchet_lint for both apps.

## Rollback

Revert the commit; status.json again stays `generated` during the Job.
