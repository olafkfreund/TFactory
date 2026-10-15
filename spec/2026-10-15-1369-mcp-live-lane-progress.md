---
status: draft
issue: 1369
intent: intent/2026-10-15-1369-mcp-live-lane-progress.md
---

# Spec: MCP `task_status` derives `lane_progress` on read

## Design

`task_status` (`apps/backend/agents/tools_pkg/tools/task_control.py:970-981`)
gets the overlay that the portal detail route already does
(`apps/web-server/server/routes/tfactory_tasks.py:281-295`):

- It lazily imports `agents.lane_progress.derive_lane_progress` and calls it
  on `_spec_dir(project_id, spec_id)`.
- If the result is not None, it replaces `status["lane_progress"]` with it.
- Any exception keeps the stored value, logged at warning level with no
  request data, as the portal does.

`derive_lane_progress` already merges the stored value for lanes the plan does
not name, and returns None when there is nothing to say. So the overlay is a
plain assignment, the same as the portal's. Both readers then give the same
answer for the same spec.

The import is lazy because `lane_progress` imports `agents.evaluator` at
module level.

## Alternatives rejected

- **Write the derived value at `evaluator_failed`.** It fixes this one status,
  but the stored value goes stale again on the next path that forgets. #1259
  chose derive-on-read for this reason.
- **A shared "status with live lanes" helper used by both readers.** The two
  readers already differ: the portal wraps the document, while MCP returns it
  raw. A helper would hold six lines and a log call. Not worth a new public
  function.

## Risks

- **Cost.** One `test_plan.json` and `verdicts.json` read per poll, plus a few
  `stat` calls. The portal pays the same on every detail read. `task_status` is
  documented as "cheap; safe to poll", and this stays in the same order.
- **The first call imports `agents.evaluator`.** That takes about 0.14 s, per
  the #1259 plan.

## Verification

- A new test in `tests/test_tfactory_mcp_tools.py`, next to
  `test_task_status_existing`:
  - It seeds a spec at `evaluator_failed` with stored
    `lane_progress = {"unit": "pending"}`, a `test_plan.json` with one `unit`
    subtask, and a fresh run artifact under `findings/runs/<id>/`.
  - It asserts that `task_status` returns `lane_progress == derive_lane_progress(spec_dir)`
    and that it is `{"unit": "executed"}`.
  - It fails on `origin/dev` and passes after.
- The existing `tests/test_tfactory_mcp_tools.py`, `tests/test_mcp_task_control.py`
  and lane-progress tests still pass.
- `ruff check`, `ruff format --check`, and the repo ratchet.
