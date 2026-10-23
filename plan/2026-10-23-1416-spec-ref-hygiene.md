---
status: draft
issue: 1416
spec: spec/2026-10-23-1416-spec-ref-hygiene.md
---

# Plan: Spec ingest ref follow-ups (cleanup, conflict, docs)

Approved decisions (from the spec):
- GC deletes `refs/tfactory/specs/<spec_dir.name>` via
  `git -C <worktree> update-ref -d` (hermetic env), only when `.worktree/.git`
  is a file, before `rmtree`; failures ignored.
- `ref` + `source_branch` together: `ValueError` in
  `create_spec_ingest_workspace` before any write, so the route returns 400
  and the MCP tool returns an error. No new MCP argument.
- Docs: `ref` in `guides/spec-sources.md`, `docs/creating-tests.md`,
  `docs/capabilities-and-usage.md`; full 40-character SHA required; the two
  fields are mutually exclusive. CHANGELOG Unreleased.
- Rejected: warn instead of reject; resolve the clone from source.json; expand
  short SHAs locally.

## Steps

1. `apps/backend/agents/liveness_sweep.py` `gc_terminal_worktrees`: delete
   this spec's ref before `rmtree`. → verify by
   `pytest tests/test_spec_ingest_workspace.py tests/test_liveness_sweep.py`.
2. `apps/backend/agents/tools_pkg/tools/task_control.py`
   `create_spec_ingest_workspace`: reject both fields next to the `_REF_RE`
   check. → verify by the new tests.
3. Tests: `tests/test_spec_ingest_workspace.py` (GC deletes own ref, keeps the
   other spec's; both-fields `ValueError`, no spec dir);
   `tests/test_specs_ingest_clone.py` (route 400 with the message; the
   existing pass-through test stops sending both fields).
4. Docs and CHANGELOG as above.

## Tests

- GC: two real ingests into one clone, one terminal → its worktree and ref
  gone, the other ref kept.
- Both fields → `ValueError`, no spec dir; route → 400 naming both fields.
- `scripts/ratchet_lint.py`, ruff, pre-commit suites.

## Rollback

Revert the PR. Refs left meanwhile are harmless; the old GC just keeps them.
