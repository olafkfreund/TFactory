---
status: draft
issue: 1599
intent: intent/2026-11-19-1599-tf-multireplica.md
---

# Spec: owner-aware inline-stage reconciler (#1599)

## Design

1. **Process id.** `agents/workspace_status.py` gets `INSTANCE_ID = f"{HOSTNAME}-{uuid4().hex[:8]}"`, fixed per process (a container restart inside the same pod gets a new id, so its predecessor's stages are correctly orphaned).
2. **Owner stamp.** `write_status_patch` adds `owner_instance=INSTANCE_ID` to the merged status whenever `fields["status"]` is `planning` or `generating`. Every inline stage already writes through this function; the field persists across later patches of that stage.
3. **Heartbeat.** Each process touches `<workspace_root>/instances/<INSTANCE_ID>` (mtime = heartbeat) every `TFACTORY_INSTANCE_HEARTBEAT_SECONDS` (15). An instance is alive when its file's mtime is younger than `TFACTORY_INSTANCE_STALE_SECONDS` (60). The shared RWO workspaces volume is already what both replicas mount. Missing file = gone. Files of dead instances older than 1 day are removed by the loop.
4. **Reconciler.** `reconcile_inline_orphans` skips a `planning`/`generating` spec when its owner is this instance or a live instance. It fails it (`control_plane_restart`, as today) when the owner's heartbeat is stale or missing. A spec with NO owner (older release) is failed only when `updated_at` is older than `TFACTORY_LEGACY_ORPHAN_SECONDS` (900, the stall deadline), so a healthy old replica mid-roll is not hit. The same function now also runs from the heartbeat loop each tick, so a dead owner is reaped within about 75 s whichever replica notices first (previously only at boot).
5. **#1368 reconciler.** `reconcile_interrupted_builds` at startup runs only when no other live instance exists (`other_instance_alive`), so a second replica never closes the first one's active build phases.
6. **Shutdown.** On lifespan shutdown (SIGTERM; uvicorn has already stopped accepting connections, so no new work is admitted) the process waits up to `TFACTORY_SHUTDOWN_DRAIN_SECONDS` (840) for its own inline stages (`owner_instance == self`, status planning/generating) to leave those statuses, polling every 2 s while the heartbeat keeps running. Leftovers are failed at once with an accurate reason ("shut down mid-<stage>") and `phase=control_plane_restart`; then its heartbeat file is removed. factory-gitops sets `terminationGracePeriodSeconds: 900` on the tfactory Deployment so the wait can finish; without it k8s kills at 30 s and the stale heartbeat reaps the stage after about 60 s.

## Alternatives rejected

- Postgres lease table: needs a migration and DB round-trips in a sync scan; the file heartbeat uses the volume both replicas already share.
- Heartbeat inside each status.json: stages write status only at boundaries; a long LLM session would look dead. A per-process file is independent of stage progress.
- Handing interrupted stages back to `queued` for another replica: there is no resume path for an inline stage (planner/gen restart from scratch via a new ingest/rerun); inventing one is out of proportion. Failing fast with an accurate reason keeps the operator-visible behaviour.
- Pod-existence check via the k8s API: needs new RBAC and a client in the sweep; the heartbeat needs neither.
- Keeping max replicas at 1 forever: loses the autoscaling the ScaledObject exists for. The cap stays until this ships (factory-gitops#406).

## Risks

- Clock/mtime skew between replicas: both use the same volume mtime and the same wall clock on one k3d node; 60 s stale vs 15 s beat leaves wide margin.
- PVC unwritable: heartbeat fails, peers may reap live stages after 60 s. Logged loudly; an unwritable volume already breaks every stage.
- Mixed-version roll: ownerless stages from the old pod survive up to 900 s without a status update, then are failed; accepted.
- A long wait slows pod termination by up to 15 min; the new pod is already Ready, the old one admits nothing new.

## Verification

New unit tests in `tests/test_liveness_sweep.py` (fail without the fix): instance B's reconcile leaves A's live planning/generating stage untouched; A with a stale/missing heartbeat is failed as `control_plane_restart`; legacy ownerless fresh stage skipped, stale one failed; `write_status_patch` stamps owner for planning only; the shutdown wait returns early when stages finish and fails leftovers after the timeout; `other_instance_alive` gates `reconcile_interrupted_builds`. Run `pytest tests/test_liveness_sweep.py tests/test_terminal_completion_wiring.py` and `scripts/ratchet_lint.py` for apps/backend and apps/web-server.
