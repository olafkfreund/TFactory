---
status: approved
issue: 1404
author: olafkfreund
---

# Intent: Spec ingest verifies a known, recorded ref

## Problem

A spec ingest without `source_branch` (target-mode) verifies the project's
shared checkout exactly as it sits on disk. Nothing refreshes it. On the live
control plane the `aifactory-demo` checkout was left on an old task branch
(91cffea, 2026-08-24), so a run tested `src/app/ordinal.py` against month-old
code and toolchain (#1400). Nothing in the run said which commit it verified.

## Proposed outcome

- Ingest accepts an optional `ref` (branch, tag or commit). Without it, the
  remote's default branch is used.
- Before verify, TFactory fetches that ref, resolves it to a commit, and
  verifies a detached per-run worktree of that commit. The shared checkout is
  never switched, reset or cleaned.
- `status.json` and the verdict (`source_sha`, served on the task GET since
  0.9.31) name the ref and the commit that were verified.
- A fetch failure or an unresolvable ref fails the run as `not_run` /
  `environment` with a clear reason. Stale code is never verified silently.
- The packed verify workspace (#1160) is the per-run worktree, and the
  worktree is reclaimed once the run is terminal.

## Affected users and systems

- `apps/backend/agents/tools_pkg/tools/task_control.py` (ingest + MCP tool)
- `apps/web-server/server/routes/specs.py` (`POST /api/specs/ingest`)
- `docs/capabilities-and-usage.md`
- Callers of the ingest API: AIFactory handoff (uses `source_branch`, unchanged),
  portal, MCP clients.

## Constraints

- Must not touch the shared checkout's HEAD, index or working tree; other
  tasks may be using it.
- `source_branch` handoffs keep working as today.
- A project root that is not a git repo has nothing to fetch; it keeps the
  on-disk behaviour unless a `ref` is named.

## Open questions

None. The user chose "fetch a known ref".
