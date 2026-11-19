---
status: approved
issue: 1594
spec: spec/2026-11-19-1594-workspace-stale-branch.md
---

# Plan: workspace refresh without a branch

Approved decisions: with no branch requested, an existing workspace clone is fetched, origin/HEAD refreshed, and the local default branch is force-reset onto the remote default tip; no `pull --ff-only` on the current branch. Branch-requested path unchanged. Callers keep their arguments.

## Steps

1. apps/web-server/server/services/project_workspace_service.py (existing-clone block, ~lines 283-295): replace the unconditional `pull --ff-only` with `if branch:` checkout + pull (as today) `else:` `remote set-head origin --auto` (cred_env) then `checkout --force -B <default> origin/<default>` (default read from `symbolic-ref --short refs/remotes/origin/HEAD`) → verify by new test.
   Traps: credential must stay in extra_env only; the finally block restoring origin must still run.
2. apps/web-server/tests/test_project_workspace_cloning.py: update the mocked no-branch test (expects set-head + checkout, no pull) and add a real-git test with a local bare repo whose checked-out branch is deleted upstream → verify by pytest.
   Traps: test git needs user config env; use file:// URL (validate_git_url may reject, check).

## Tests

`cd apps/web-server && pytest tests/test_project_workspace_cloning.py tests/test_specs_ingest_clone.py tests/test_auto_fix_pull_on_poll.py tests/test_clone_security.py`; `python scripts/ratchet_lint.py`.

## Rollback

Revert the squash commit.

## Deviation

Codex review (P2): a detached checkout left local `main` stale, which AIFactory/worktree base detection uses. Step 1 now resets the local default branch (`checkout -B`) instead of detaching.
