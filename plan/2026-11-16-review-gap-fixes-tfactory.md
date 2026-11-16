---
status: approved
issue: 1507
spec: spec/2026-11-16-review-gap-fixes-tfactory.md
coders:
  1: you (lead, one small file edit)
reviewers:
  - codex
---

# Plan: ephemeral-storage limit in kube_sandbox

Approved decisions: add `ephemeral_storage_limit="4Gi"` to `kube_sandbox.build_job_manifest`, limit 4Gi, request pinned "0", no caller changes.

## Steps

1. apps/backend/tools/runners/kube_sandbox.py (signature ~l.205, resources ~l.241): add the parameter and the limits entry → verify by tests/test_lane_job_resources.py.
   Traps: none.
2. tests/test_lane_job_resources.py, tests/test_kube_sandbox.py: new regression test; update the two assertions that pinned the old limits dict → verify by pytest on both files.

## Tests

`PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest tests/test_lane_job_resources.py tests/test_kube_sandbox.py tests/test_verify_dispatch.py tests/test_execution_sandbox_seam.py`

## Rollback

Revert the PR; the parameter is additive.
