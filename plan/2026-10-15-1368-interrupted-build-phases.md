---
status: draft
issue: 1368
spec: spec/2026-10-15-1368-interrupted-build-phases.md
---

# Plan: close build phases interrupted by a web-server restart

## Approved decisions (self-contained)

- A build runs as a web-server subprocess. Its phase is `active` in
  `task_logs.json` until the process ends it. If the server or pod dies
  mid-phase, the phase stays `active`, and `load_spec_metadata`
  (`apps/web-server/server/routes/tasks.py:517-531`) reports `in_progress`
  forever. Recover and PATCH cannot override it, because only `done` and
  `completed` beat an active phase.
- Fix it on the writer side. Do not change `load_spec_metadata`.
  - **Startup** closes every `active` phase as `failed`, with `completed_at` and
    `interrupted: true`. The task then reads `human_review` / `errors`.
  - **Recover** resets `active` phases to `pending`, so the plan status it
    writes, `backlog` by default, is what the task shows.
- Gate the startup step on the existing `settings.INLINE_ORPHAN_RECONCILE_ENABLED`.
  Startup errors follow the #774 block's pattern.
- Close phases in the main spec dir and in the task worktree's spec copy
  (`get_worktree_spec_dir`).

## Steps

1. **`apps/web-server/server/routes/tasks.py`**, after `load_spec_metadata`
   (which ends near `:640`; put the new code right after the function). Add:
   - `close_active_phases(spec_dir: Path, to_status: str) -> list[str]`:
     - Read `spec_dir / "task_logs.json"`. On a missing file, `OSError` or
       `json.JSONDecodeError`, return `[]`. If the parsed value or its
       `phases` is not a dict, return `[]`.
     - For each phase dict with `status == "active"`: set `status = to_status`,
       and if `to_status == "failed"` also set
       `completed_at = datetime.now(UTC).isoformat()` and `interrupted = True`.
     - Write back with `json.dumps(logs, indent=2)` only if something changed.
       Return the changed names.
   - `reconcile_interrupted_builds() -> list[tuple[str, list[str]]]`:
     - For each project in `load_projects().values()`, take
       `Path(p["path"]) / ".tfactory" / "specs"`. If it is missing, skip it.
     - For each spec subdir, call `close_active_phases(d, "failed")`. If
       `get_worktree_spec_dir(project_path, d.name)` returns a dir, call it on
       that too.
     - Collect `(spec_id, phases)` for every spec where either call changed
       something.
     - Wrap each project in `try/except OSError: continue`.

   → `ruff check`, plus step 4's test.
   Traps:
   - `datetime` is already imported as `from datetime import datetime`; add
     `UTC` to that import.
   - Use `safe_component`-validated names only. Directory names come from
     `iterdir`, so pass `d.name`.
   - mypy `--strict` ratchet: annotate everything.

2. **`apps/web-server/server/main.py:171-197`**: after the #774
   `if settings.INLINE_ORPHAN_RECONCILE_ENABLED:` block, add a second block
   under the same condition:
   - `from server.routes.tasks import reconcile_interrupted_builds`, using the
     import style of nearby imports in main.py.
   - `closed = await asyncio.to_thread(reconcile_interrupted_builds)`.
   - Log a warning `"#1368 startup reconcile: closed interrupted build phase(s) in %d task(s): %s"`
     if `closed`, else an info line.
   - `except _STARTUP_BUGS: logger.exception(...); raise`, and
     `except Exception: logger.exception(... "(continuing)")`, as in #774.

   → `pytest apps/web-server/tests -q -k "startup or lifespan"`, if any exist.
   Traps: do not merge it into the #774 `try`, so that one failing does not
   skip the other.

3. **`apps/web-server/server/routes/execution.py:630-631`**: right after
   `sync_worktree_to_main_spec(project_path, spec_id)`, add
   `close_active_phases(spec_dir, "pending")` and the same call for
   `get_worktree_spec_dir(project_path, spec_id)` if it returns a dir. Extend
   the `from .tasks import ...` line (`:21`).

   → step 4's test.
   Traps: the call must come after the process is terminated, which is the
   block at `:617-628`.

4. **New `tests/test_interrupted_build_phases.py`**, using the import pattern
   of `tests/test_tasks_correlation_issue.py` (`sys.path.insert` of
   `apps/web-server`). Cases:
   - An `active` `coding` phase: `load_spec_metadata` gives `in_progress`.
     After `close_active_phases(d, "failed")` it gives `human_review` with
     `reviewReason == "errors"`, and the phase has `interrupted: True`.
   - No active phase: returns `[]`, and the file is byte-identical. Malformed
     JSON: returns `[]`.
   - `reconcile_interrupted_builds` with `server.routes.tasks.load_projects`
     monkeypatched to `{"p": {"path": str(tmp)}}`: closes the main spec and
     its worktree copy, and returns `[("001-x", ["coding"])]`.
   - The recover route: call `asyncio.run(execution.recover_task("p:001-x"))`
     with these monkeypatched on `server.routes.execution`:
     - `load_projects` → the same dict;
     - `get_agent_service` → `SimpleNamespace(running_tasks={})`;
     - `emit_task_status` → an async no-op.

     Then `load_spec_metadata` gives `backlog`.
   - Check that the recover and `close_active_phases` cases fail on
     `origin/dev`: run the file in a detached `origin/dev` worktree, not with
     `git stash`. The stash list is shared with other worktrees.

   → `apps/backend/.venv/bin/python -m pytest tests/test_interrupted_build_phases.py tests/test_tasks_correlation_issue.py -q`

## Tests

```
cd apps/backend && PYTHONPATH=. .venv/bin/python -m pytest \
  ../../tests/test_interrupted_build_phases.py ../../tests/test_tasks_correlation_issue.py -q
ruff check / ruff format --check on the four files
PATH=$PWD/apps/backend/.venv/bin:$PATH python scripts/ratchet_lint.py --base origin/dev \
  --package apps/backend --package apps/web-server --package scripts
```

All of these should pass, and the ratchet should report no new violations.

## Rollback

Revert the commits. Set `APP_INLINE_ORPHAN_RECONCILE_ENABLED=0` to disable the
startup step without a deploy. Phases that were already closed stay closed,
which is the correct terminal state.
