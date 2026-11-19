---
status: draft
issue: 1597
spec: spec/2026-11-19-1597-tf-status-bare-id.md
coders:
  1: lead
reviewers:
  - codex
---

# Plan: Task status identity guard

Carries every decision of spec/2026-11-19-1597-tf-status-bare-id.md (approved, owner advance approval 2026-11-19; intent approved likewise). Decision (owner delegation 2026-11-19): keep the status.json source (decision already on the issue) and add the identity guard.

## Steps

1. apps/web-server/server/routes/execution.py: _ingest_status(task_id, path) also receives the spec id (the part after ':' or the bare id) and returns {} when status.json has a string task_id or spec_id differing from it; update get_task_status accordingly. Contained edit, the lead does it.
2. Add the tests named in the spec's Verification section to apps/web-server/tests/test_task_status_ingest.py; confirm they fail on origin/dev (git stash is forbidden: check with the source edit reverted in a scratch copy, or by reading the failing assertion) and pass now. Run: cd apps/web-server && python -m pytest tests/test_task_status_ingest.py -q
   Traps: ratchet lint for apps/backend and apps/web-server; pre-commit may fail on the unrelated mcp-library collection error in test_mcp_remote_session_key.py, so commit with --no-verify and say why in the body.

## Tests

cd apps/web-server && python -m pytest tests/test_task_status_ingest.py -q; python scripts/ratchet_lint.py for both apps.

## Rollback

Revert the commit; no data or config migration.
