---
status: draft
issue: 1368
intent: intent/2026-10-15-1368-interrupted-build-phases.md
---

# Spec: close build phases interrupted by a web-server restart

## Design

Both writers live in the web-server, next to the reader they correct.

1. **Helper** in `apps/web-server/server/routes/tasks.py`, next to
   `load_spec_metadata` (`:470`):

   `close_active_phases(spec_dir: Path, to_status: str) -> list[str]`

   - Reads `spec_dir / "task_logs.json"` and sets every phase whose `status` is
     `active` to `to_status`.
   - Only for `failed`, it stamps `completed_at` (UTC ISO) and an
     `interrupted: true` marker.
   - It writes the file back only if a phase changed, and returns the changed
     phase names.
   - A missing or unreadable file, or a file that is not a dict, returns `[]`
     and is never raised on.

2. **Startup reconcile** `reconcile_interrupted_builds() -> list[tuple[str, list[str]]]`,
   in the same file:
   - Walks `load_projects()` → each project's `.tfactory/specs/*`
     (`get_specs_for_project`'s layout, `tasks.py:242-245`), plus the task
     worktree copy from `get_worktree_spec_dir` (`:291`).
   - Calls `close_active_phases(dir, "failed")` on each, and returns
     `(spec_id, phases)` for every spec it changed.
   - In `apps/web-server/server/main.py` lifespan, it runs right after the #774
     inline-orphan reconcile (`:171-197`), behind the same
     `settings.INLINE_ORPHAN_RECONCILE_ENABLED`, through `asyncio.to_thread`.
   - It logs a warning listing the closed tasks. Errors are handled the same
     way as the #774 block: `_STARTUP_BUGS` re-raise, anything else is logged
     and boot continues.
   - It is safe at startup for the same reason #774 is: on the RWO workspaces
     volume the previous pod is gone, so its build subprocesses are gone too,
     and this process has started none yet.

3. **Recover route** (`routes/execution.py:581-696`):
   - After the process is terminated and the worktree is synced, it calls
     `close_active_phases(dir, "pending")` on the main spec dir and on the
     worktree spec dir.
   - A `pending` phase is invisible to the reader (`tasks.py:522-558` checks
     only `active`, `failed` and `completed`). So the `test_plan.json` status
     the route writes is what the task shows, including `backlog`.
   - If the recover auto-restarts the task, the new run's `start_phase` sets
     the phase `active` again, as normal.

The reader `load_spec_metadata` is unchanged.

## Alternatives rejected

- **Reader-side liveness check** (an `active` phase wins only if
  `agent_service.is_running(task_id)` is true). A build can also be started
  outside the server (`run.py` from a shell or the MCP), and those would read
  as not running. It would also hide the dead state rather than record it.
- **Let any explicit plan status beat an active phase in the reader.** That
  changes precedence for every live task. A kanban drag of a running task
  would hide that it is running.
- **Mark the phase `failed` in recover too.** The reader turns a failed phase
  into `human_review`, which overrides the user's `backlog`, so the route
  would still not do what it says.
- **Fix it in `fix-stuck-tasks.py`.** That is a manual repair, and the audit
  notes it never touches `task_logs.json`.

## Risks

- **A build process that survives a restart** (local dev with a supervisor
  that restarts only uvicorn) has its phase closed as `failed` while it still
  runs. Its next `start_phase` / `end_phase` overwrites the file, so it
  self-corrects at the next phase boundary. In the deployed pod, a restart
  kills every child process. The existing flag disables the reconcile.
- **A failed phase that a resumed run never reruns** keeps the task at
  `human_review` / `errors` after that run completes. This is how a genuinely
  failed phase already behaves, and it is not new.
- **Startup cost:** one small JSON read per spec. The #774 reconcile already
  walks the same volume.

## Verification

- New `tests/test_interrupted_build_phases.py`:
  - **`load_spec_metadata` before and after `close_active_phases`.** An
    `active` phase reads `in_progress`. After `close_active_phases(…, "failed")`
    it reads `human_review` / `errors`. After `close_active_phases(…, "pending")`
    with plan status `backlog`, it reads `backlog`.
  - **No-op cases.** No `active` phase leaves the file byte-identical. A
    malformed file returns `[]`.
  - **`reconcile_interrupted_builds`** with a monkeypatched `load_projects`
    closes a stranded spec in both the main and the worktree dir.
  - **Recover route** through FastAPI `TestClient`, with `load_projects` and
    `get_agent_service` patched: the task reads `backlog` afterwards.
    Fails on `origin/dev`.
- The existing `tests/test_tasks_correlation_issue.py` and the web-server tests
  for main startup still pass.
- `ruff check`, `ruff format --check`, and the repo ratchet (mypy `--strict`
  on changed files).
