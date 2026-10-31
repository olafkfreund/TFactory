---
status: draft
issue: 1479
intent: intent/2026-10-31-1479-worktree-exact-project-rbac.md
---

# Spec: worktree resolve writes by exact project id; viewer check on worktree reads

## Design (user decision: exactly the issue's Fix)

1. `resolve_uncommitted_conflicts` and `resolve_git_merge_conflicts`
   (`apps/web-server/server/routes/tasks_worktree.py`): replace the "Find the
   task across all projects" loop with the merge-preview pattern: 400 on a bare
   id, `project_id, spec_id = task_id.split(":", 1)`, `safe_component(spec_id)`,
   `_exact_project_path`, `safe_spec_dir(project_path, spec_id)` must exist,
   worktree at `.tfactory/worktrees/tasks/<spec_id>`. Not-found responses keep
   their current shape.
2. Add `dependencies=[Depends(require_task_access("viewer"))]` to the
   merge-preview, status and diff GET routes. Shadow/enforce follows the
   existing `RBAC_ENFORCE_TASK_ROUTES` flag.

## Alternatives rejected

- Keep the scan but make the RBAC key resolve the found project: still lets a
  bare id pick an arbitrary project; differs from #1474.
- Enforce the viewer check now: the other task routes are in shadow; switching
  is a separate flag flip.

## Risks

A client sending a bare id to the resolve routes now gets 400. The web UI sends
`task.id` (`project_id:spec_id`); no other caller exists in the repo.

## Verification

- Bare id → 400 on both writes (`tests/test_tasks_worktree_route.py`).
- `tests/test_task_route_rbac.py`: the three GETs are listed with role viewer;
  with the flag off, a caller outside org A on each of the five routes yields
  an `authz.task.would_deny` audit.
- ruff, ratchet, full pytest in CI.
