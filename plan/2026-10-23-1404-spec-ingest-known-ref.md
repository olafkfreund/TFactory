---
status: draft
issue: 1404
spec: spec/2026-10-23-1404-spec-ingest-known-ref.md
---

# Plan: Spec ingest verifies a known, recorded ref

Approved decisions (user: fetch a known ref):
- Ingest takes optional `ref` (branch, tag or commit). Ref used =
  `source_branch or ref`, else the remote default branch from
  `git ls-remote --symref origin HEAD`. Non-git root with no ref: unchanged.
- Materialize with the existing `_add_spec_worktree` (per-spec fetch ref,
  detached `<spec_dir>/.worktree`, HEAD == fetched sha). Never touch the
  shared checkout.
- Record `source_ref` + `source_sha` in `source.json` and `status.json`
  (`source_sha` is the verdict commit on the task GET).
- Failure: `status: failed`, `phase: source_checkout_failed`,
  `source_checkout_error`, plus `verify: not_run`, `failure_kind: environment`.
- Packing (`dispatch_verify_job(project_dir=worktree)`) and cleanup
  (`gc_terminal_worktrees`) are reused unchanged.
- Rejected: keep verifying the on-disk checkout and record/warn; reset the
  shared checkout; local-only rev-parse of a commit.

## Steps

1. `apps/backend/agents/tools_pkg/tools/task_control.py` `_add_spec_worktree`
   (~277-367): neutral "ref" wording in messages; add `_default_branch(base)`
   helper (ls-remote --symref, hermetic env). → verify by
   `pytest tests/test_spec_ingest_workspace.py`.
   Traps: existing test asserts "source_branch" or "not a git repo" in the
   warning; keep "not a git repo".
2. Same file, `create_spec_ingest_workspace` (~399-630): `ref` kwarg; compute
   the fetch ref; materialize for target-mode too; write `source_ref`,
   `source_sha`, `verify`/`failure_kind` on failure. MCP tool
   `task_create_from_spec` (~867-969): `ref` property, passed through.
   → verify by the new tests below.
   Traps: target-mode ingests in other tests use non-git tmp dirs; they must
   keep working.
3. `apps/web-server/server/routes/specs.py`: `SpecIngestRequest.ref` field,
   passed to `create_spec_ingest_workspace`; update `source_branch`
   description (no longer "whatever is checked out"). → verify by
   `pytest tests/test_specs_ingest_clone.py`.
4. `docs/capabilities-and-usage.md` §7 and `CHANGELOG.md` Unreleased.
5. Tests in `tests/test_spec_ingest_workspace.py` and
   `tests/test_specs_ingest_clone.py` (see Tests).

## Tests

- Stale-branch checkout verifies the default-branch head.
- Explicit `ref` branch and commit sha honoured.
- Shared checkout HEAD and `git status --porcelain` unchanged.
- Bad ref → failed / not_run / environment, planner not scheduled.
- `gc_terminal_worktrees` reclaims a target-mode worktree.
- Route passes `ref` through.
- Full backend + web-server suites (pre-commit hook), `scripts/ratchet_lint.py`,
  ruff.

## Rollback

Revert the PR. Specs ingested meanwhile keep their `.worktree`; the GC reaps it.
