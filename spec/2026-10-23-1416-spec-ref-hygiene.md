---
status: draft
issue: 1416
intent: intent/2026-10-23-1416-spec-ref-hygiene.md
---

# Spec: Spec ingest ref follow-ups (cleanup, conflict, docs)

## Design

User decision (task): delete the ref in GC; reject `ref` + `source_branch`
with a 400; document `ref` and the full-SHA rule.

1. **Ref cleanup.** In `gc_terminal_worktrees`, before `rmtree` of a terminal
   spec's `<spec_dir>/.worktree`, when `.worktree/.git` is a file (a real git
   worktree, not a plain dir) run
   `git -C <worktree> update-ref -d refs/tfactory/specs/<spec_dir.name>`
   with the hermetic env `_add_spec_worktree` uses. Refs under `refs/` are
   shared by all worktrees, so this deletes the ref in the base clone. The ref
   name is this spec's id only, so no other spec's ref is touched. Failure is
   ignored (best-effort, like the `rmtree`).
2. **Conflict.** `create_spec_ingest_workspace` raises
   `ValueError("send either ref or source_branch, not both")` before any
   workspace is written. The route already maps `ValueError` to 400 with the
   message; the MCP tool already maps it to `_format_error`. The MCP tool takes
   no `source_branch`, so it cannot send both today; the shared check covers
   it without new MCP code.
3. **Docs.** `guides/spec-sources.md` (entry points section) and
   `docs/creating-tests.md` (REST/MCP front door) gain `ref`: branch, tag or
   full 40-character commit SHA; default is the remote's default branch;
   `source_branch` and `ref` are mutually exclusive. `docs/capabilities-and-usage.md`
   gets the same two facts. CHANGELOG Unreleased entry.

## Alternatives rejected

- **Warn instead of reject on both fields.** The task chose 400; a warning in
  the response is easy to miss.
- **Resolve the base clone from `source.json` / project registry in GC.** More
  code; the worktree's own `.git` file already points at its clone.
- **Expand short SHAs locally with `rev-parse`.** Trusts a stale local object
  set, which #1404 rejected; document the full-SHA rule instead.

## Risks

- A caller that sent both fields now gets 400 instead of a run on
  `source_branch`. None known (AIFactory sends only `source_branch`).
- If GC runs while the worktree's `.git` file is broken, the ref lingers (same
  as today). Harmless.

## Verification

- Test: two real ingests into one clone; mark one terminal; GC removes its
  worktree and its ref; the other spec's ref is kept.
- Test: ingest with both fields raises `ValueError` and writes no spec dir;
  route test returns 400 with the message.
- `scripts/ratchet_lint.py`, ruff, backend + web-server suites.
