---
status: draft
issue: 1391
intent: intent/2026-10-22-1391-packed-lanes-no-pvc.md
---

# Spec: packed verify — nested lane Jobs get their workdir by object store, not PVC

Owner decision (#1160, 2026-10-22): **option B** — fix the nested-lane PVC
forwarding before flipping `TFACTORY_PACK_WORKSPACE`. This spec covers only that
fix; the k3d agent node, the toggle flip and the `repo_pvc` removal stay on #1160.

## Design

Chosen: **(i) pack the lane workdir and give the nested Job `workspace_uri`.**

1. `agents/verify_dispatch.py` (Job env builder): when `cfg.workspace_uri` is set,
   do **not** forward `TFACTORY_WORKSPACES_PVC` and do **not** set
   `TFACTORY_DATA_ROOT`; set `TFACTORY_LANE_PACK=1` instead. Unpacked dispatch
   builds the exact same env as today.
2. `agents/nix_env.nix_runner_from_env`: when `TFACTORY_LANE_PACK` is truthy,
   build `KubeJobSandbox(repo_pvc=None, pack_workdir=True)` even if a PVC env
   leaked through.
3. `tools/runners/kube_sandbox.KubeJobSandbox` gains `pack_workdir: bool = False`
   (carried through `with_manifest_kw`). When set and `workdir` is given,
   `_run_async`:
   - packs `workdir` with the vendored `artifact_store.pack_workspace` (verify
     Job already has the `S3_*` env) under a per-lane key (`job_id` = the lane
     Job name, role `workspace`);
   - presigns a GET for that object and a PUT for a sibling `<name>-out` object,
     expiring after the lane timeout plus a margin;
   - builds the manifest with `workspace_uri=<uri>` (existing `emptyDir`
     branch, no PVC) and adds the two presigned URLs to the lane container env;
   - wraps the lane command: `curl | tar -xz` into `/work`, run the original
     commands, keep their exit code, drop absolute symlinks (they would fail the
     safe extractor), `tar -cz` `/work` and `curl -T` it to the PUT URL, exit
     with the lane's code;
   - after the Job, unpacks the `-out` object back into `workdir` with the
     vendored path-traversal-guarded `unpack_workspace`, so callers find junit,
     coverage and screenshots exactly where they look today; then deletes both
     objects (best effort).
   A failed pack/presign returns a failed `JobRunResult` with the reason (the
   lane is marked errored, never graded); a missing/failed result object appends
   a diagnostic to the output and returns a failed result.

Why this is the smallest correct option: the leaf lane gets exactly the scratch
tree the verify Job built, with no credentials (two single-object presigned URLs
only), using `curl`/`tar` that the `nixos/nix` base image already ships. All the
code lives at the one seam where `repo_pvc` is decided today.

## Alternatives rejected

- **(ii) Run lane commands in-process inside the verify Job.** The verify Job
  runs the TFactory runtime image, which has no `nix` (`agents/verify_dispatch.py`
  module note, #466: "The orchestration Job does NOT itself need nix"; the root
  `Dockerfile` installs none). `TFACTORY_NIX_IN_IMAGE` refers to the *lane*
  image's `/nix`. Without nix the lanes fall back to the host runner and lose the
  RFC-0005 flake toolchain, so verdicts would be graded on a different
  environment. Making the verify image carry nix is a far larger change.
- **initContainer/finalizer containers on the runtime image** for unpack and
  push-back: needs S3 credentials in the lane pod and a second image pull per
  lane; presigned URLs do the same with neither.
- **Keep forwarding the PVC**: is the bug.

## Risks

- Lane pods must reach the object-store endpoint. The verify Job already does
  with the same pod labels (`app: tfactory-sandbox`), so the per-task
  NetworkPolicy should already allow it; not provable on this cluster until the
  toggle is flipped. Unpacked mode is unaffected either way.
- boto3 presign signature must be SigV4 for MinIO (boto3's default for presign).
- Larger SUTs (e.g. `node_modules`) add tar/upload time per lane.
- Presigned PUT objects carry no `role=workspace` tag; deleted after collection,
  so lifecycle rules are not relied on.

## Verification

- Unit: a packed verify Job manifest has no `TFACTORY_WORKSPACES_PVC` /
  `TFACTORY_DATA_ROOT` and has `TFACTORY_LANE_PACK`; unpacked env unchanged.
- Unit: `nix_runner_from_env` with `TFACTORY_LANE_PACK` → `repo_pvc is None`,
  `pack_workdir` set.
- Unit: `KubeJobSandbox(pack_workdir=True)` run with a fake store and fake k8s
  API → manifest has no PVC volume, has `emptyDir` + presigned env; a result
  object written by the fake lane is unpacked into `workdir` (results collected).
- Existing `tests/test_verify_dispatch.py`, `tests/test_nix_env.py`,
  kube_sandbox tests pass unchanged.
- On-cluster proof stays with #1160 step 5 (second node + toggle flip).
