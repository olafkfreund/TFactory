---
status: draft
issue: 1450
author: olafkfreund
---

# Intent: task-route role checks and API-key scope caps

Closes #1450. Refs olafkfreund/Factory#312. Slice S1 of the hub plan
`olafkfreund/Factory:plan/2026-10-27-312-fleet-rbac-role-model.md`.

## Problem

Task routes have no role or org check, so any authenticated principal (an org
`viewer`'s JWT included) can start, stop, approve, reject or merge any task.
`POST /api/keys` lets anyone below `member` mint `mcp:write`/`api:full` keys,
and an `acw_` key keeps its scopes after its owner is demoted or removed.

## Proposed outcome

- Mint cap: an `acw_` principal cannot mint; below `member` only `mcp:read`.
- Auth cap: a key's scopes are capped by its owner's current role in
  `key.org_id` (no membership, no scopes), on both the MCP and REST paths.
- Every task-keyed write route computes `require_task_access("member")`.
  Behind `RBAC_ENFORCE_TASK_ROUTES` (default off = shadow mode: log and audit
  would-deny, never change a response, fail open on lookup errors).

## Affected users and systems

TFactory web-server: `routes/api_keys.py`, `mcp_remote/auth.py`, task routes,
`config.py`. `acw_` key holders (prod had 0 `api_keys` rows on 2026-10-27,
recounted before merge). No response changes for task routes while the flag
is off.

## Constraints

No gitops change, no deploy, no lockout: TF projects in `projects.json` carry
no `org_id`, so enforcement waits for slice S6. No JWT role claim trusted.

## Remaining slices (hub plan)

S2 PFactory route checks; S3 evidence tooling; S4 admin revoke; S5 PF/TF
remote MCP authz (#1456); S6 project `org_id` backfill and flag flip; S7 first
access review; S8 enforcement follow-ups.

## Open questions

None.
