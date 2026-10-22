---
status: draft
issue: 1393
author: olafkfreund
---

# Intent: packed lanes — uncollected results, leaked objects, push-back deadline

## Problem

Follow-ups from the review of #1392 (#1391, Refs #1160). On the packed lane path
(`TFACTORY_LANE_PACK=1`, `KubeJobSandbox(pack_workdir=True)`):

1. nix_env lanes read the verdict from the `__PYTEST_EXIT` / `__PW_EXIT` /
   `__GOTEST_EXIT` marker in stdout and ignore `res.ok`. A lane that passed but
   whose results were not collected back still counts as a pass, with junit and
   coverage missing.
2. If `create_namespaced_job` or the polling loop raises, cleanup never runs, so
   the packed input (the SUT) and any output object stay in the bucket. The
   output object is also uploaded without the `role=workspace` lifecycle tag.
3. The push-back runs inside the lane's own `activeDeadlineSeconds`, so a lane
   that runs close to its timeout is killed before its results are uploaded.

## Proposed outcome

- A packed lane whose results were not collected is graded as an infra
  `not_run` (environment failure), never as a pass.
- Both lane objects are deleted whatever fails, and the output object carries
  the same `role=workspace` tag as the input.
- The lane command gets the timeout minus a reserve; the push-back always has
  that reserve left.

## Affected users and systems

TFactory backend: `tools/runners/kube_sandbox.py`, `agents/stability_runner.py`.
Packing is off by default, so production is unaffected until #1160 step 5.

## Constraints

- Unpacked mode unchanged.
- Mypy ratchet (`scripts/ratchet_lint.py --base origin/dev`) passes.
- Presigned URLs are never logged.

## Open questions

None. User decision: fix all three items.
