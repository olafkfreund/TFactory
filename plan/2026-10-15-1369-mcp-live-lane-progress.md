---
status: approved
issue: 1369
spec: spec/2026-10-15-1369-mcp-live-lane-progress.md
---

# Plan: MCP `task_status` derives `lane_progress` on read

## Approved decisions (self-contained)

- MCP `task_status` returns the stored `lane_progress`. After `evaluator_failed`
  that value is still the all-`pending` seed, while the portal derives the
  real one through `derive_lane_progress` (#1259).
- Fix it in the reader: `task_status` overlays the derived value exactly as
  the portal route does (`apps/web-server/server/routes/tfactory_tasks.py:281-295`).
- Use a lazy import, because `lane_progress` imports `agents.evaluator`. On
  any exception, keep the stored value and log a warning with no request data.
- No new shared helper. Nothing is written back to `status.json`.

## Steps

1. `apps/backend/agents/tools_pkg/tools/task_control.py:977-981`
   (`task_status`, after the `status is None` check): add

   ```python
   # Derived on read, like the portal detail view, so it cannot go stale --
   # e.g. after evaluator_failed the stored value is still the creation seed
   # (#1369, #1259). A failure keeps the stored value.
   try:
       from agents.lane_progress import derive_lane_progress  # noqa: PLC0415

       live = derive_lane_progress(_spec_dir(project_id, spec_id))
   except Exception:  # noqa: BLE001 - additive, never breaks the tool
       logging.getLogger(__name__).warning(
           "lane_progress derivation failed; serving the stored value",
           exc_info=True,
       )
       live = None
   if live is not None:
       status["lane_progress"] = live
   ```

   Use the module's existing logger if it has one.

   → `pytest tests/test_tfactory_mcp_tools.py tests/test_mcp_task_control.py tests/test_lane_progress.py -q`

   Traps:
   - mypy `--strict` ratchet on this file.
   - Do not log `task_id` (log injection).
2. `tests/test_tfactory_mcp_tools.py`: add
   `test_task_status_lane_progress_is_derived_after_evaluator_failed` after
   `test_task_status_existing`. It should:
   - create the task the same way as that test;
   - then rewrite the spec's `status.json` to `status: evaluator_failed` with
     `lane_progress: {"unit": "pending"}`;
   - write `test_plan.json` with `phases[0].subtasks = [{"id": "u1", "lane":
     "unit", "files_to_create": ["tests/test_u1.py"]}]`;
   - then create `findings/runs/u1/out.txt`, after the plan, so its mtime is
     fresh;
   - assert the body's `lane_progress == {"unit": "executed"}` and that it
     equals `derive_lane_progress(spec_dir)`.

   Prove that it fails on the `origin/dev` code: copy the test file into a
   detached `origin/dev` worktree. Never use `git stash`, because the stash
   list is shared with other worktrees.

   → the same pytest command.

## Tests

```
cd apps/backend && PYTHONPATH=. .venv/bin/python -m pytest ../../tests/test_tfactory_mcp_tools.py \
  ../../tests/test_mcp_task_control.py ../../tests/test_lane_progress.py -q
ruff check / ruff format --check on both files; repo ratchet vs origin/dev
```

## Rollback

Revert the commit. This is read-only behaviour, and nothing stored changes.
