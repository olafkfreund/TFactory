---
status: draft
issue: 1631
author: olafkfreund
---

# Intent: Task status must say running while a verify Job is in flight

## Problem

factory-lab-cloud PR #1: GET /api/tasks/{id}/status showed gen_functional_complete, is_running:false for ~13 min while the kubejob verify ran, then jumped to triager_complete. dispatch_verify_job never updates the control-plane status.json, which stays status=generated (not an active status).

## Proposed outcome

While a verify Job is in flight status.json reads status=evaluating, phase=verify_job_running, so is_running is true and the phase names the verify.

## Affected users and systems

apps/backend/agents/verify_dispatch.py, the status route and MCP proxy (read status.json), the liveness sweep.

## Constraints

Smallest correct change; tests that fail without the fix; no change to verdict semantics; other teams edit TFactory in parallel, keep the diff narrow.

## Open questions

None. Decision (owner delegation 2026-11-19): take the recommended option recorded in the issue.
