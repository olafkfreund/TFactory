---
status: approved
issue: 1393
spec: spec/2026-10-22-1393-packed-lane-hardening.md
---

# Plan: packed lanes — uncollected results, leaked objects, push-back deadline

Approved decisions carried from the spec (user decision: fix all three items of
#1393; unpacked mode unchanged):

- Not collected: `kube_sandbox._collected` defangs `^__[A-Z_]+=` marker lines
  (prefix `[not collected] `) and appends `__TF_LANE_NOT_COLLECTED__`;
  `stability_runner._ENVIRONMENT_MARKERS` includes that marker, so the verdict
  becomes infra `not_run` via the existing `environment` path.
- Cleanup: `_pack_lane` returns `(commands, uri, extra_env, collect, discard)`;
  `discard` runs in `_run_async`'s outer `finally`. PUT presign carries
  `Tagging=role=workspace`; curl adds `-H 'x-amz-tagging: role=workspace'` when
  the URL has no `x-amz-tagging=` query param (SigV4 signed header).
- Budget: lane command runs as `timeout -k 10 <budget> bash -c <quoted cmds>`,
  `reserve = min(120, timeout // 4)`, `budget = max(1, timeout - reserve)`.
- Rejected: per-caller `res.ok` checks; separate push-back step; raising
  `activeDeadlineSeconds`; control-side tagging.

## Steps

1. `apps/backend/tools/runners/kube_sandbox.py` `_collected` (~397), `_pack_lane`,
   `_collect_lane`, `_run_async` (~420-620), and
   `apps/backend/agents/stability_runner.py` `_ENVIRONMENT_MARKERS` (~106):
   implement the three decisions above. → verify by
   `pytest tests/test_kube_sandbox.py tests/test_stability_runner.py`.
   Traps: `kubernetes_asyncio` import stays inside `_run_async`; presigned URLs
   never logged; do not edit drift-checked `tools/runners/artifact_store.py`;
   mypy ratchet `python scripts/ratchet_lint.py --base origin/dev`.
2. Tests in `tests/test_kube_sandbox.py`: (a) collect failure → output has no
   line starting `__PYTEST_EXIT=`, has the marker, and
   `classify_pytest_failure(out, 1) == "environment"`; (b) `create_namespaced_job`
   raises → fake bucket empty; (c) wrapped command has `timeout -k 10 <budget>`
   with budget < timeout; (d) PUT presign Params carry `Tagging`. → verify by the
   same pytest command.
   Traps: reuse `_LaneS3` / `_fake_kube`.

## Tests

`cd apps/backend && .venv/bin/pytest ../../tests/test_kube_sandbox.py ../../tests/test_stability_runner.py -q` — all pass;
ratchet passes.

## Rollback

Revert the PR; packing is off by default.

## Deviations

- Step 1: instead of re-indenting `_run_async` under a new try/finally, the Job
  body moved unchanged into `_run_job`; `_run_async` packs, awaits `_run_job`,
  and calls `discard` in its `finally`. Same behaviour, smaller diff.
  `_collect_lane` now only unpacks; deletion lives in `discard`.
