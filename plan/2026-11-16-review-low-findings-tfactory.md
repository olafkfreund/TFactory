---
status: draft
issue: 1511
spec: spec/2026-11-16-review-low-findings-tfactory.md
coders:
  1: lead
  2: lead
reviewers:
  - codex
---

# Plan: TFactory LOW findings

Decisions carried from the approved spec: (1) pass the signing variables only when `TFACTORY_TRIAGER_GIT_SIGN` is on; (2) #1517 refuted by AIFactory's receiving-side checks, no change; (3) cap `originator` / `agent_identity` at 128 chars with charset `^[\w.:@/+\- ]+$`. Steps are small, so the lead does them; Codex reviews.

## Steps

1. apps/backend/tools/git_writer.py `_run`: add `_SIGN_KEEP` and extend `keep` when `_sign_enabled()` → `pytest tests/test_git_writer.py`. Traps: vendored `agent_sandbox` untouched.
2. apps/web-server/server/routes/specs.py `SpecIngestRequest`: `max_length` and `pattern` on both fields → `pytest tests/test_specs_ingest_clone.py tests/test_spec_ingest_workspace.py`. Traps: none.
3. Tests in tests/test_git_writer.py and tests/test_specs_ingest_clone.py as in the spec.

## Tests

`pytest tests/test_git_writer.py tests/test_specs_ingest_clone.py tests/test_schema_drift.py tests/test_triager_completion_webhook.py tests/test_completion_envelope.py tests/test_spec_ingest_workspace.py tests/test_regression_notify.py`; `ruff check` and `ruff format --check` on touched files.

## Rollback

Revert the PR; both changes are additive and local.
