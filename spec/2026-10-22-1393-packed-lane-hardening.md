---
status: approved
issue: 1393
intent: intent/2026-10-22-1393-packed-lane-hardening.md
---

# Spec: packed lanes — uncollected results, leaked objects, push-back deadline

User decision: fix all three items of #1393 for packed lanes; unpacked mode unchanged.

## Design

1. **Not collected = errored.** All nix_env lane callers (pytest, go, gradle,
   maven, jest, playwright, deploy) and the evaluator take the verdict from
   `__<NAME>_EXIT=` / `__<NAME>_RUN=` marker lines. Fix it once, where the
   result is made: `kube_sandbox._collected`, on a collect failure, defangs every
   line matching `^__[A-Z_]+=` (prefixes it with `[not collected] `) and appends
   the wire marker `__TF_LANE_NOT_COLLECTED__`. Every marker parser then finds no
   exit code and falls back to failure (code 1). `stability_runner`'s
   `_ENVIRONMENT_MARKERS` gains `__TF_LANE_NOT_COLLECTED__`, so
   `classify_pytest_failure` returns `environment` and
   `confidence.apply_environment_override` records the verdict as `not_run`
   (infra), the same bucket as a failed Nix env build (#1343).
2. **Cleanup always runs.** `_pack_lane` returns `collect` (unpack only) and
   `discard` (delete both keys, best effort). `_run_async` calls `collect` via
   `_collected` inside the try and `discard` in the outer `finally`, so a raise
   from `create_namespaced_job` or polling still deletes both objects. The
   presigned PUT carries `Tagging=role=workspace`; when the signature lists
   `x-amz-tagging` as a signed header (SigV4: no `x-amz-tagging=` query param)
   the lane's curl sends `-H 'x-amz-tagging: role=workspace'`.
3. **Push-back budget.** The lane command runs as
   `timeout -k 10 <budget> bash -c '<cmds>'` with
   `budget = max(1, timeout - reserve)`, `reserve = min(120, timeout // 4)`.
   The Job keeps `activeDeadlineSeconds = timeout`, so the tar + upload has at
   least `reserve - 10` seconds left.

## Alternatives rejected

- Make every nix_env caller check `res.ok`: seven call sites plus evaluator
  parsers; misses future callers. One fix at the source is smaller.
- Separate push-back container / step with its own deadline: needs a sidecar or
  a second Job and S3 creds or more presigned URLs; much larger.
- Raising `activeDeadlineSeconds` to `timeout + reserve`: the caller's poll
  budget is `timeout`, so it would stop waiting before the push-back ends.
- Tagging the output object from the control side after collection: does not
  help exactly when the control side dies, the case lifecycle rules exist for.

## Risks

- `timeout` must exist in the runner image (nixos/nix ships coreutils).
- A lane that ran past `budget` now exits 124 with no marker: graded as a
  failure / retried, as a lane killed by the deadline was before.
- `apply_environment_override`'s reason text names a missing target URL; it is
  generic enough to be acceptable here and is unchanged.

## Verification

`pytest tests/test_kube_sandbox.py tests/test_stability_runner.py` with new tests:
not-collected output has no parseable exit marker and classifies `environment`;
a raising `create_namespaced_job` leaves the fake bucket empty; the wrapped
command contains `timeout -k 10 <budget>`; the PUT presign carries the tag.
Ratchet: `python scripts/ratchet_lint.py --base origin/dev`.
