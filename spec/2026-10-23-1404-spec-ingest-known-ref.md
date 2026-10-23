---
status: approved
issue: 1404
intent: intent/2026-10-23-1404-spec-ingest-known-ref.md
---

# Spec: Spec ingest verifies a known, recorded ref

## Design

User decision: **fetch a known ref.**

The per-spec worktree machinery already exists for `source_branch` (#742):
`_add_spec_worktree` in `apps/backend/agents/tools_pkg/tools/task_control.py`
fetches into `refs/tfactory/specs/<spec_id>`, adds a `--detach` worktree at
`<spec_dir>/.worktree`, checks HEAD equals the fetched sha, and the whole
pipeline (planner, generator, `dispatch_verify_job` pack, rerun) is threaded
with that worktree as the SUT dir. Target-mode simply skipped it. The change
reuses it for target-mode:

1. `create_spec_ingest_workspace(..., ref=None)`. The ref to materialize is
   `source_branch or ref`; when both are absent and `project_root` is a git
   repo, it is the remote's default branch, resolved with
   `git ls-remote --symref origin HEAD` (`ref: refs/heads/<name>`).
   A non-git `project_root` with no ref keeps the on-disk behaviour (nothing
   to fetch; recorded as `source_ref: null`).
2. `_add_spec_worktree` fetches the ref (branch, tag or full commit sha, all
   valid as a fetch refspec source) and returns the commit. Its messages say
   "ref" rather than "source_branch" where they are shared.
3. Recorded: `source.json` gets `source_ref` (resolved branch/tag/sha name)
   and `source_sha` (commit, already served as the verdict's commit on
   `GET /api/tasks/{id}` since 0.9.31) and `worktree`. `status.json` gets
   `source_ref` and `source_sha`.
4. Failure: any fetch / resolve / worktree failure fails the spec at ingest as
   today (`status: failed`, `phase: source_checkout_failed`,
   `source_checkout_error`) and now also `verify: not_run`,
   `failure_kind: environment`, the convention `_fail_unpacked_dispatch` uses.
   The planner is not scheduled.
5. API: `SpecIngestRequest.ref` (web-server) and the MCP
   `task_create_from_spec` `ref` property, passed through. Documented in
   `docs/capabilities-and-usage.md`; `source_branch` wins when both are sent.
6. Packing and cleanup need no new code: `dispatch_verify_job` packs the
   `project_dir` it is given (the worktree, with its base clone via
   `_git_main_repo`), and `gc_terminal_worktrees` already removes
   `<spec_dir>/.worktree` for terminal specs. Tests pin both for target-mode.

## Alternatives rejected

- **Keep verifying the on-disk checkout and just record / warn.** Still verifies
  stale code; a warning in status.json is not read by the merge gate. Rejected
  by the user.
- **Reset the shared checkout to the default branch before verify.** Yanks the
  tree from under concurrent tasks (#742). Rejected by the constraints.
- **Fetch a commit only via local `rev-parse`.** Would accept a stale local ref
  without asking the remote; fetch is the source of truth.

## Risks

- A git project root without an `origin` remote (local-only repo) now fails
  target-mode ingest as `not_run/environment`. That is intended (no known ref)
  but is a behaviour change for such projects; the reason names the cause.
- One extra `ls-remote` round-trip per target-mode ingest.
- Worktree disk use per target-mode spec, reclaimed by the existing GC.

## Verification

Backend tests in `tests/test_spec_ingest_workspace.py`:
- a shared checkout left on a stale branch still verifies the default-branch
  head (worktree content and `source_sha` = origin default head);
- an explicit `ref` (branch and commit sha) is honoured;
- the shared checkout's HEAD and `git status --porcelain` are unchanged;
- a bad ref → `status: failed`, `verify: not_run`, `failure_kind: environment`,
  planner not scheduled;
- `gc_terminal_worktrees` reclaims a target-mode worktree.
Web-server: the route passes `ref` through. Full backend + web-server suites
and `scripts/ratchet_lint.py` pass.
