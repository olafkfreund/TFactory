---
status: approved
issue: 1160
author: Olaf Krasicki-Freund
---

# Intent: Packed verify workspaces, end to end — toggle, control-plane unpack, then drop the PVC

## Problem

Verify concurrency is bounded by **storage, not nodes**: each verify Job
co-mounts the spec worktree's RWO PVC, so it must land on the node holding it.
A second node adds no verify capacity, and a shared mount makes Job isolation
a matter of path discipline. (RWX/NFS was measured and rejected — the export
cannot serve git.)

#1158/#1159 built the Job side: the workspace can be packed to MinIO, the Job
runs on an `emptyDir`, restores it, and pushes results back (#1239); #1243's
review fixed the verdict ordering (#1245). None of it is reachable — there is
no toggle — and the #1243 review found the blocker for turning it on:

**Nothing on the control plane reads the pushed-back workspace.** The only
`unpack_workspace` caller outside `artifact_store.py` is
`verify_workspace.restore_workspace`, which runs *inside the Job*. After a
packed run the durable `job_states` row is correct, but `status.json`,
findings, evidence, screenshots, junit and coverage on the PVC stay at their
pre-dispatch content. Every PVC reader — portal task list/detail
(`tfactory_tasks.py`), handback routes, liveness (`agent_service.py`), the
reaper's spec write (`verify_dispatch.py`), and now `lane_progress` (#1259) —
would show the spec as `evaluating` forever.

## Proposed outcome

In order, each observable before the next starts:

1. **Unpack on the control plane:** after a packed Job reaches a terminal
   row, the pushed-back workspace is restored into the data root exactly
   where the PVC readers look — once, idempotently, retried on failure, and
   tolerant of a workspace that was never pushed back (`stuck` rows).
2. **The toggle:** `TFACTORY_PACK_WORKSPACE` (`true` = pack, `emptyDir`, any
   node; `false`/**unset** = today's PVC co-mount). Unset changes nothing.
3. **Proven on real specs:** with the toggle on, a verify Job is observed on
   a node that does **not** hold the worktree, and afterwards `status.json` on
   the PVC matches the Job's verdict and the evidence tree is present.
4. **Only then, remove `repo_pvc`** from `kube_sandbox` — the PVC path stays
   as the fallback until step 3 has run green on real specs.

## Affected users and systems

- `apps/backend/agents/verify_dispatch.py` (dispatch worker_ref,
  `reconcile_and_reap_once`), `agents/verify_workspace.py`,
  `tools/runners/artifact_store.py`, `kube_sandbox`
- Every PVC reader listed above (they become correct, unchanged)
- MinIO (object lifecycle for pushed-back workspaces)
- The p510 cluster: scheduling, a second node, the deploy config that sets the
  toggle (factory-gitops)
- #1159 (its acceptance — "evidence present after a packed run" — is step 3
  here)

## Constraints

- **Unset = unchanged.** This lands while the demo path is live; a toggle
  that changes behaviour by existing is not a toggle.
- A row dispatched before the flip keeps co-mount semantics: the packed flag
  is the `workspace_uri` recorded on that row at dispatch, never inferred
  from the current toggle.
- A failed unpack must not be swallowed (the row is already terminal, so
  `stuck` is unreachable): log loudly, leave the done-marker unset, retry.
- Step 4 (deleting `repo_pvc`) must not happen before step 3 is observed —
  it removes the way back.
- Cluster changes (enabling the toggle in the deployed config) need the
  operator's go-ahead at that point, not only this intent's approval.

## Open questions

1. One task or two? Recommend **two PRs under this one intent/spec**: (a)
   unpack + toggle (steps 1–2, default off, fully testable locally); (b) the
   `repo_pvc` removal (step 4) after the step-3 observation.
2. Close #1159 now as subsumed by this task's step 3, or keep it open until
   step 3 is observed? Recommend close-with-pointer when this intent is
   approved, so one issue tracks one acceptance.
3. MinIO retention for pushed-back workspace objects after a successful
   unpack — delete, or keep for N days as evidence? Needs an owner decision.
