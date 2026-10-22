---
status: draft
issue: 1391
author: olafkfreund
---

# Intent: packed verify — nested lane Jobs must not co-mount the RWO workspaces PVC

Refs #1160 (step 5 blocker, owner chose option B: fix this before flipping
`TFACTORY_PACK_WORKSPACE`).

## Problem

A packed verify Job (`workspace_uri` set) has no PVC: its workspace is an
`emptyDir` restored from the object store. It still forwards
`TFACTORY_WORKSPACES_PVC` and `TFACTORY_DATA_ROOT` to the nested per-lane Nix
Jobs (`agents/verify_dispatch.py` env allowlist), and
`agents/nix_env.nix_runner_from_env` builds `KubeJobSandbox(repo_pvc=<PVC>)`. So
each lane Job co-mounts the RWO PVC and is pinned to its node, and it reads the
PVC copy of the per-run scratch path — which only exists in the verify Job's
`emptyDir`, so on the PVC it is empty. Packing the verify Job buys no
node-agnosticism while its lanes stay pinned, and the lanes would run against
the wrong tree.

## Proposed outcome

In packed mode a lane Job mounts no PVC and receives exactly the workdir the
verify Job prepared; what the lane writes into it (junit, coverage,
screenshots) comes back to the verify Job, so results are collected as today.
Unpacked mode is unchanged.

## Affected users and systems

TFactory verify Jobs and their nested Nix lane Jobs (k3d `factory` namespace);
`agents/verify_dispatch.py`, `agents/nix_env.py`,
`tools/runners/kube_sandbox.py`. No factory-gitops or cluster change.

## Constraints

- Unpacked (co-mounted) dispatch must produce the same env and manifests.
- No object-store credentials in the leaf lane Job: it runs generated code.
- The vendored `tools/runners/artifact_store.py` is drift-checked; do not edit.
- mypy ratchet: no net-new `--strict` errors per changed file.

## Open questions

None (approved unseen for the 2026-10-22 decision wave).
