---
status: approved
issue: 1368
author: olafkfreund
---

# Intent: a build interrupted by a web-server restart stops showing `in_progress`

## Problem

A build task runs as a subprocess of the web-server
(`apps/web-server/server/services/agent_service.py`, `running_tasks`). While a
phase runs, the task logger marks it `active` in the spec's `task_logs.json`
(`apps/backend/task_logger/logger.py:155`). It becomes `completed` or `failed`
only when the process ends the phase (`logger.py:193`).

If the web-server or its pod dies mid-phase, the subprocess dies with it and the
phase stays `active` forever. Nothing at startup looks at `task_logs.json`. The
startup reconcile (`main.py:171-197`) only handles verify specs' `status.json`
(#774).

The reader then lets an active phase win over everything else
(`apps/web-server/server/routes/tasks.py:517-531`), so `GET /api/tasks` reports
`in_progress` with no process behind it. The two ways out do not work:

- **Recover** (`routes/execution.py:581-696`) exists for exactly this case ("a
  task shows as running but the process has died"). It rewrites
  `test_plan.json` status, but the reader honours a plan status over an active
  phase only for `done` / `completed` (`tasks.py:586-592`). `backlog` or
  `human_review` are ignored.
- **PATCH** of the status (`tasks.py:1326`) has the same limit.

The failure is silent: nothing logs it, and the card looks busy.

Found by the Factory#971 terminal-status audit. PFactory has the same shape
(filed separately).

## Proposed outcome

- After a web-server restart, a build phase left `active` by the previous
  process is closed as `failed`. The task shows `human_review` with
  `reviewReason: errors`, the reader's existing meaning for a failed phase. The
  restart logs which tasks it closed.
- Recovering a task clears its interrupted phase, so the status the user
  recovers to is the status the task shows.

## Affected users and systems

- TFactory web-server: startup lifespan, the recover route, the task list API.
- Portal users who see build tasks on the kanban.
- Deployed single-pod setup (RWO workspaces volume). Build subprocesses die with
  the pod.

## Constraints

- Must not touch a phase that a live process owns. At startup the new process
  has started nothing yet. In recover, the route has just terminated the
  process.
- Keep the reader's precedence rules (`load_spec_metadata`) as they are. This
  is a writer-side fix.
- Startup must not fail because of a malformed `task_logs.json`.
- Use the existing `APP_INLINE_ORPHAN_RECONCILE_ENABLED` switch. Do not add a new flag.

## Open questions

None. Choices taken:

- **Startup closes interrupted phases as `failed`.** The run did die, and a
  human should look at it.
- **Recover resets them to `pending`.** That is a status the log schema already
  has (`TaskLogPhaseStatus`, `frontend-web/src/shared/types/task.ts:61`), so the
  user's chosen status wins, as the route promises.
