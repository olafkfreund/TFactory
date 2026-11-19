---
status: draft
issue: 1594
author: olafkfreund
---

# Intent: workspace refresh must not depend on the checked-out branch (#1594)

## Problem

`POST /api/specs/ingest` with `repo` returns 502 "could not clone repository" when the shared workspace clone is still checked out on a branch that was deleted on the remote. `clone_or_update(branch=None)` runs `git pull --ff-only` on whatever is checked out, which fails with "no such ref was fetched". Every later ingest for that repo fails the same way.

## Proposed outcome

Refreshing an existing workspace clone with no branch requested always succeeds when the remote is reachable: it fetches and moves to the remote default branch. Behaviour when a branch is requested is unchanged. The e2e level-1 proof (AIFactory demo PR #610) can run.

## Affected users and systems

`clone_or_update` callers: `routes/specs.py` (three), `routes/projects.py`, `services/auto_fix_service.py`. The tfactory pod's shared workspace PVC.

## Constraints

- Smallest change in `project_workspace_service.py`; no credential or URL-validation change.
- Existing branch-requested paths (`clonedBranch`, `source_branch`, `project.branch`) keep their behaviour.

## Open questions

None.
