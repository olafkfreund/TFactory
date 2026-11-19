---
status: draft
issue: 1594
intent: intent/2026-11-19-1594-workspace-stale-branch.md
---

# Spec: workspace refresh without a branch

## Design

In `clone_or_update` (apps/web-server/server/services/project_workspace_service.py), existing-clone path, after `fetch --prune origin`:

- `branch` given: unchanged (`checkout <branch>` then `pull --ff-only`).
- `branch` is None: `remote set-head origin --auto` (refresh origin/HEAD), then `checkout --detach --force origin/HEAD`. No pull on the stale current branch; the clone sits detached on the remote default tip.

Callers: specs.py:220 and :310 pass a branch (unchanged); specs.py:363 (`_clone_named_repo`), projects.py:558 (`project.branch` may be None) and auto_fix_service.py:593 (`clonedBranch` may be None) hit the new path; for them the clone was made on the default branch, so the result is the same tip, now robust to a deleted branch. The per-spec ref is fetched later by the ingest flow, so `_clone_named_repo` needs no PR-ref fetch.

## Alternatives rejected

- Fetch `refs/pull/<n>/head` inside clone_or_update: it has no PR parameter and the ingest flow already fetches the ref later; extra surface for no gain.
- `pull --ff-only origin <default>`: still needs the default name and leaves a local branch to go stale.
- Catch the pull failure and retry: treats the symptom.

## Risks

Detached HEAD on shared workspaces for no-branch callers; the forced checkout discards uncommitted workspace edits (workspaces are cache clones). A remote with no HEAD makes `set-head` fail and raises GitOperationError as any git failure does.

## Verification

New test with a local bare repo: clone, check out a branch, delete it upstream, call `clone_or_update(branch=None)`; fails before the fix, passes after, HEAD equals remote default tip. Update the mocked no-branch test. Run ratchet_lint and the workspace/ingest tests.
