---
status: approved
issue: 1458
author: olafkfreund
---

# Intent: close the task-route RBAC gaps before S6

Closes #1458. Refs olafkfreund/Factory#312 (hub plan
`plan/2026-10-27-312-fleet-rbac-role-model.md`; follow-up to slice S1, #1457).

## Problem

1. `routes/project_authz.py` `ProjectAccessChecker._authorize` rolls back the
   request session only on the would-deny path. Once projects carry `org_id`,
   every allowed write checks out a pooled connection for the membership query
   and holds it, idle in a transaction, for the whole handler (merge,
   create-pr, resolve-conflicts are long). The default QueuePool (5 + 10)
   runs dry at about 15 concurrent long merges (same class as AIFactory #1936).
2. Task-keyed writes the route dependency does not reach:
   - `mcp_stdio/router.py` task proxies (create-and-run, start, stop, recover,
     approve-plan, create-pr, merge) call the handlers directly; only the
     `acw_` scope is checked, so an org-A key can act on org-B tasks.
   - `rmux/bridge.py` agent-console attach, detach and the WebSocket (attach
     gives keyboard input to the live agent).
   - `routes/projects.py` `/{project_id}/tasks` create, logs watch/unwatch,
     archive, unarchive.
3. Log watch/unwatch need `member` in `tasks_logs.py`; they are read-like, so a
   viewer could not watch logs once enforcement is on.

## Proposed outcome

- The authz decision releases its connection on every path (allow, deny, error)
  before the handler runs.
- Every route above computes the project-org decision for the caller (the
  key's principal for `acw_`), shadow-mode behind `RBAC_ENFORCE_TASK_ROUTES`
  like the existing dependency.
- An `acw_` key bound to org A is refused on an org-B project through
  `mcp_stdio` regardless of the flag.
- Log watch/unwatch and the read-only console WebSocket need `viewer`
  (matches PFactory #950); writes need `member`.

## Affected users and systems

TFactory web-server: `routes/project_authz.py`, `routes/projects.py`,
`routes/tasks_logs.py`, `rmux/bridge.py`, `mcp_stdio/router.py`, tests.
No response changes while the flag is off, except the always-on cross-org key
check, which cannot fire today (no TF project has `org_id`; prod has 0 keys).

## Constraints

No gitops change, no deploy, no lockout. Flag stays default off. No JWT role
claim trusted. Avoid PFactory #950's bugs: no second pooled connection,
no variable unset in an `except`, no mixed return types, no test fixtures
that patch a reloaded module.

## Open questions

None (pre-approved for the 2026-10-30 wave). Remaining hub slices: S5 (remote
MCP authz, #1456), S6 (project `org_id` backfill + flag flip), S8 follow-ups.
