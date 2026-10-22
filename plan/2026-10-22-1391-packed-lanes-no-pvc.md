---
status: draft
issue: 1391
spec: spec/2026-10-22-1391-packed-lanes-no-pvc.md
---

# Plan: packed verify — nested lane Jobs get their workdir by object store, not PVC

Approved decisions carried from the spec:

- Owner chose option B on #1160: fix nested-lane PVC forwarding first.
- Design (i): in packed mode the verify Job forwards `TFACTORY_LANE_PACK=1`
  instead of `TFACTORY_WORKSPACES_PVC` / `TFACTORY_DATA_ROOT`;
  `nix_runner_from_env` then builds `KubeJobSandbox(repo_pvc=None,
  pack_workdir=True)`; the sandbox packs `workdir` to the object store, runs the
  lane Job with `workspace_uri` (emptyDir, no PVC) plus two presigned URLs
  (GET input, PUT output), and unpacks the output back into `workdir`.
- Rejected: (ii) in-process lanes — the verify Job image has no nix;
  init/final containers with S3 creds in the lane pod.
- Unpacked mode: env and manifests unchanged. No gitops/cluster change. Do not
  edit the drift-checked `tools/runners/artifact_store.py`.

## Steps

1. `apps/backend/agents/verify_dispatch.py` ~744-808 (`build_verify_job_manifest`
   env): when `cfg.workspace_uri`, skip `TFACTORY_WORKSPACES_PVC` in the nix
   allowlist loop, skip the `TFACTORY_DATA_ROOT` append, append
   `TFACTORY_LANE_PACK=1`. → verify by `pytest tests/test_verify_dispatch.py`.
   Traps: comment the why in one line; the allowlist loop must keep its order
   for the unpacked case.
2. `apps/backend/agents/nix_env.py` ~281-313 (`nix_runner_from_env`): read
   `TFACTORY_LANE_PACK` (same truthy set as `nix_in_image`); when on, pass
   `repo_pvc=None, pack_workdir=True`. → verify by `pytest tests/test_nix_env.py`.
   Traps: `nix_env` is not drift-checked; keep `nix_in_image` untouched.
3. `apps/backend/tools/runners/kube_sandbox.py` `KubeJobSandbox` (~397-560):
   add `pack_workdir: bool = False` to `__init__` and `with_manifest_kw`; add
   `_pack_lane(name, commands, workdir, timeout)` returning
   `(commands, workspace_uri, extra_env, cleanup)` (uses
   `agents.verify_workspace._open_store`-style lazy `artifact_store` import,
   `pack_workspace`, `store._client().generate_presigned_url` for
   `get_object`/`put_object`, expiry `timeout + 600`) and
   `_collect_lane(workdir, out_uri)` (`unpack_workspace` into `workdir`, then
   best-effort `delete_object` for both keys). In `_run_async`, when
   `pack_workdir and workdir`, call them around the Job, build the manifest with
   `workspace_uri=` and no `repo_pvc`, append the presigned env to the `lane`
   container; a pack failure returns `JobRunResult(False, 1, reason)`
   without creating a Job; a collect failure appends `[kube-sandbox] lane
   results not collected: ...` to the output and returns a failed result.
   Wrapped lane command (one string):
   `set -e; curl -fsS "$TF_LANE_IN_URL" | tar -xz -C /work; set +e; ( <orig> );
   rc=$?; find /work -type l -lname '/*' -delete; tar -czf /tmp/tf_lane_out.tgz
   -C /work . && curl -fsS -T /tmp/tf_lane_out.tgz "$TF_LANE_OUT_URL" || echo
   '[kube-sandbox] lane push-back failed'; exit $rc`.
   → verify by `pytest tests/test_kube_sandbox.py`.
   Traps: `kubernetes_asyncio` import stays inside `_run_async`; mypy ratchet
   (`python scripts/ratchet_lint.py`) — no net-new strict errors; the presigned
   URLs are env, never logged.
4. Tests: `tests/test_verify_dispatch.py` (packed env has no PVC/DATA_ROOT, has
   `TFACTORY_LANE_PACK`; unpacked unchanged), `tests/test_nix_env.py` (lane-pack
   env → `repo_pvc is None`, `pack_workdir`), `tests/test_kube_sandbox.py`
   (fake store: `_pack_lane` uploads the workdir, wraps commands, env has both
   URLs; `_collect_lane` restores a lane-written `junit.xml` into `workdir`).

## Tests

`cd apps/backend && .venv/bin/pytest ../../tests/test_verify_dispatch.py
../../tests/test_nix_env.py ../../tests/test_kube_sandbox.py -q` → all pass;
`ruff check` on touched files clean; `python scripts/ratchet_lint.py` passes.

## Rollback

Revert the PR. With `TFACTORY_PACK_WORKSPACE` off (the deployed default) none of
the new code paths run.
