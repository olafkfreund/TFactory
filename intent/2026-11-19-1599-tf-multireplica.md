---
status: approved
issue: 1599
author: olafkfreund
---

# Intent: a second TFactory replica must not fail the first one's live stages (#1599)

## Problem

KEDA scaled deploy/tfactory 1->2 under parallel load (2026-11-19 06:40 UTC). The new pod's startup reconciler (`liveness_sweep.reconcile_inline_orphans`, #774) failed every spec in `planning`/`generating` as `control_plane_restart`, assuming "any inline stage was orphaned by the pod that died". Those stages were running in the OTHER, healthy pod. Three tasks died. The same happens on a RollingUpdate (maxSurge 1) whenever a planning/generating stage is active while old and new pod overlap. Failing the spec also makes it terminal, so the startup worktree GC then deletes the live stage's `.worktree` (the vanished worktree seen on the issue). The ScaledObject comment that TFactory is safe above 1 replica since #465 is wrong for in-process stages. A stopgap caps replicas at 1 (factory-gitops#406).

A SIGTERM on the other side is also lossy: a scaled-down or rolled pod exits with in-process stages still running and the default 30 s grace, so they are lost and only noticed by the next reconciler or the stall watchdog.

## Proposed outcome

- Every in-process stage records which process owns it. A process proves it is alive by a heartbeat on the shared workspaces volume.
- The reconciler fails an inline stage as `control_plane_restart` only when its owner is gone (no live heartbeat), never because this process lacks it in memory.
- On SIGTERM a pod stops admitting new work and lets its own inline stages finish within the grace period; whatever is left is failed immediately with an accurate reason instead of lingering.

## Affected users and systems

`agents/workspace_status.py`, `agents/liveness_sweep.py`, `apps/web-server/server/main.py` (lifespan), config, factory-gitops `apps/tfactory` (terminationGracePeriodSeconds). Every user of parallel verify runs.

## Constraints

- Smallest correct change; no new dependency; the RWO workspaces PVC is the only shared medium besides Postgres.
- Fail-safe direction: when unsure whether an owner is alive, treat it as alive (the stall watchdog and Job deadlines are the backstop).
- No live-cluster action; replica cap changes are the coordinator's call after release.

## Open questions

Decision (owner delegation 2026-11-19): take the recommended option, a per-process id plus a heartbeat file, over a Postgres lease table (needs a migration and is heavier). Specs with no owner recorded (written by an older release, possibly a healthy old replica during rollout) are failed only when their `updated_at` is older than a legacy window, never on sight. The second startup reconciler with the same flaw, `reconcile_interrupted_builds` (#1368, routes/tasks.py), is in scope: it runs only when no other live instance heartbeats.
