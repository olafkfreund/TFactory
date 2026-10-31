---
status: approved
issue: 1479
spec: spec/2026-10-31-1479-worktree-exact-project-rbac.md
coders:   # the lead named Codex as coder for this lane
  1: codex
  2: codex
---

# Plan: worktree resolve writes by exact project id; viewer check on worktree reads

Approved decisions: resolve-uncommitted and resolve-git-merge require
`project_id:spec_id` (bare → 400) and find the project with the existing
`_exact_project_path` (no scan); merge-preview, status and diff get
`require_task_access("viewer")` in shadow mode. Callers: the web UI sends
`task.id` (`project_id:spec_id`).

## Steps

1. `apps/web-server/server/routes/tasks_worktree.py`:
   a. `resolve_uncommitted_conflicts` (~l.737-767) and
      `resolve_git_merge_conflicts` (~l.1025-1055): before reading
      projects.json, `if ":" not in task_id: raise HTTPException(400, detail="Task ID must include project ID (format: project_id:spec_id)")`;
      split, `spec_id = safe_component(spec_id)`; replace the scan loop with
      `project_path = _exact_project_path(projects_data, project_id)`; if
      none or `safe_spec_dir(project_path, spec_id)` missing →
      `{"success": False, "error": f"Task {task_id} not found"}`;
      `worktree_path = project_path / ".tfactory" / "worktrees" / "tasks" / spec_id`.
      Copy `get_worktree_merge_preview` (l.67-90). Leave the rest as is.
   b. Add `dependencies=[Depends(require_task_access("viewer"))]` to the
      `@router.get` of merge-preview, status and diff.
   → verify by `ruff check` + `ruff format --check` on the file.
   Traps: `_exact_project_path` is defined later in the module (fine at call time).
2. Tests:
   a. `tests/test_tasks_worktree_route.py`: `test_resolve_writes_need_exact_project`:
      with `_projects`, bare `s1` → HTTPException 400 on both handlers;
      `nope:s1` → `{"success": False, "error": "Task nope:s1 not found"}`.
   b. `tests/test_task_route_rbac.py`: add the three GETs to `EXPECTED` with
      role viewer (count 21 → 24); add
      `test_shadow_worktree_routes_log_would_deny_for_other_org`: mount
      `tasks_worktree.router` on the env app, point `tasks_worktree.get_data_dir`
      at an empty tmp dir, call each of the five routes as a principal outside
      org A on `p1:s1`, assert each yields one `authz.task.would_deny` audit.
   → verify by running both test files (lead runs them: Codex's sandbox hangs
   on aiosqlite) and confirming the new tests fail on origin/dev.

## Tests

`cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_tasks_worktree_route.py ../../tests/test_task_route_rbac.py -q`; CI runs the full suite and ratchet.

## Rollback

Revert the PR. No schema or config change.
