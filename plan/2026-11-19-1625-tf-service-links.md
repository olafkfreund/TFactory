---
status: draft
issue: 1625
spec: spec/2026-11-19-1625-tf-service-links.md
coders:
  1: lead   # one-line edits across three builders, cheaper than a handoff
reviewers:
  - codex
---

# Plan: enableServiceLinks false on every Job pod

Decision from the spec: set `enableServiceLinks: false` in every builder that creates a TFactory Job pod; no other change.

## Steps

1. apps/backend/tools/runners/kube_sandbox.py:264 pod_spec: add `"enableServiceLinks": False` → tests/test_kube_sandbox.py
2. apps/backend/tools/runners/job_dispatch.py:606 pod_spec: same → tests/test_job_dispatch_service_links.py
3. portal_testing/dispatch.py:345 pod spec: same → test in tests/test_cloud_portal_run.py or new test file
   Traps: ratchet_lint.py for apps/backend and apps/web-server; commit with --no-verify (unrelated mcp collection error).

## Tests

`.venv/bin/pytest tests/test_kube_sandbox.py tests/test_verify_dispatch.py tests/test_job_dispatch_service_links.py`; ruff.

## Rollback

Revert the commit.
