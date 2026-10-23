---
status: draft
issue: 1416
author: olafkfreund
---

# Intent: Spec ingest ref follow-ups (cleanup, conflict, docs)

## Problem

Review follow-ups from #1414 (#1404):

1. Every git ingest now leaves `refs/tfactory/specs/<id>` in the shared clone.
   Nothing deletes it, so its objects are never garbage-collected.
2. An ingest that sends both `ref` and `source_branch` silently drops `ref`.
3. `guides/spec-sources.md` and `docs/creating-tests.md` do not mention `ref`.
4. A short commit SHA as `ref` fails (fetch needs a full SHA); no doc says so.

## Proposed outcome

- `gc_terminal_worktrees` deletes `refs/tfactory/specs/<id>` when it removes
  that spec's worktree, and never another spec's ref.
- An ingest with both `ref` and `source_branch` is rejected with HTTP 400 and a
  clear message; the MCP `task_create_from_spec` path rejects it the same way.
- Both docs document `ref` and say a commit must be a full 40-character SHA.

## Affected users and systems

- `apps/backend/agents/liveness_sweep.py` (`gc_terminal_worktrees`)
- `apps/backend/agents/tools_pkg/tools/task_control.py` (ingest validation)
- `POST /api/specs/ingest` callers sending both fields (none known: the
  AIFactory handoff sends only `source_branch`).
- `guides/spec-sources.md`, `docs/creating-tests.md`

## Constraints

- GC stays best-effort: a failed ref delete never raises or blocks worktree removal.
- Keep the diff local; another lane works on TFactory concurrently.

## Open questions

None. The task names rejection (400) over a warning.
