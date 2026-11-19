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

## Decisions (copied from the approved spec)


execution.py `_ingest_status` takes the requested spec id and returns {} when the loaded status.json carries a string task_id or spec_id that differs from it (absent keys are accepted, so old files still work). The bare-id lookup stays an exact literal join with the exactly-one-match rule. The colon form uses the same guard with its spec part. The MCP proxy shares get_task_status so is covered.

Test matrix: tests/test_task_status_ingest.py: two concurrent tasks in one project each return their own phase (guards the exact join); a status.json whose task_id names another task, and separately one whose spec_id does, return no phase for the requested id, via both the bare id and the project:spec form. The mismatch cases fail on current dev.

## Steps

1. apps/web-server/server/routes/execution.py: _ingest_status(task_id, path) also receives the spec id (the part after ':' or the bare id) and returns {} when status.json has a string task_id or spec_id differing from it; update get_task_status accordingly. Contained edit, the lead does it.
2. Add the Test matrix above to apps/web-server/tests/test_task_status_ingest.py; confirm they fail on origin/dev (git stash is forbidden: check with the source edit reverted in a scratch copy, or by reading the failing assertion) and pass now. Run: cd apps/web-server && python -m pytest tests/test_task_status_ingest.py -q
   Traps: ratchet lint for apps/backend and apps/web-server; pre-commit may fail on the unrelated mcp-library collection error in test_mcp_remote_session_key.py, so commit with --no-verify and say why in the body.

## Tests

cd apps/web-server && python -m pytest tests/test_task_status_ingest.py -q; from the repo root, after committing: python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server.

## Rollback

Revert the commit; no data or config migration.
