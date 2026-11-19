---
status: draft
issue: 1597
author: olafkfreund
---

# Intent: Task status for a bare id never shows another task's data

## Problem

GET /api/tasks/demo-624d-20261119/status returned data of l-go-pr1-wc-lab-god-20261119 while both ran in one project. Bare-id resolution (#1611) is meant to be exact but the response must be provably the requested task's.

## Proposed outcome

A bare id resolves only to the exact spec dir, and a status.json that names a different task is never returned for it.

## Affected users and systems

apps/web-server/server/routes/execution.py (GET /api/tasks/{id}/status, MCP proxy).

## Constraints

Smallest correct change; tests that fail without the fix; no cluster changes; base dev; release lead ships 0.13.3.

## Open questions

None. Decision (owner delegation 2026-11-19): keep exact-dir resolution (re-checked by a two-task test; the code already joins the literal dir, so the cross-task read could not be reproduced from source) and add an identity guard: ignore a status.json whose task_id/spec_id differs from the requested spec id. Cause evidence for the live case is requested on the issue.
