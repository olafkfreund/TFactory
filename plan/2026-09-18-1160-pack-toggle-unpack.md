---
status: approved
issue: 1160
spec: spec/2026-09-18-1160-pack-toggle-unpack.md
---

# Plan: Packed verify workspaces — PR A (restore + toggle, default off)

This plan covers **PR A only**. PR B (`repo_pvc` removal) gets its own plan
revision after the step-3 cluster observation, and is not started before.

Approved decisions (self-contained):

- **Toggle** `TFACTORY_PACK_WORKSPACE`: `true`/`1`/`yes` (case-insensitive)
  → pack; anything else or unset → co-mount (unchanged). Read at the single
  call site `gen_functional._run_dispatch_blocking` (`gen_functional.py:531`),
  passed as `dispatch_verify_job(..., pack_workspace=...)`. Pack stays
  fail-open.
- **Packed flag** = `workspace_uri` in `worker_ref`: after a successful pack,
  `dispatch_verify_job` re-records `<spec_dir>/worker_ref.json`
  (`_record_spec_worker_ref`) and the durable row's `worker_ref` with
  `workspace_uri`. Never inferred from the current toggle.
- **Push-back marker**: in `verify_pipeline.py`, immediately before
  `push_back_workspace` (`:321`), the Job writes
  `<spec_dir>/.pushed_back.json` `{job_id, pushed_at}` inside `packed_root`.
- **Restore sweep**, a second pass in `verify_dispatch.reconcile_and_reap_once`
  (never raises, independent of `recover_in_flight`):
  - walk `<data_root>/workspaces/*/specs/*/worker_ref.json` with
    `workspace_uri`, no `.workspace_restored` sentinel;
  - `s.get(job_id)` must be terminal (`is_terminal_record`), else skip;
  - fetch + extract to a temp dir with the vendored `unpack_workspace`
    (unchanged — `tools/runners/artifact_store.py` is not modified);
  - no `.pushed_back.json` with this `job_id` in the archive's spec subtree →
    restore nothing, sentinel `{"restored": false, "reason": "no push-back"}`,
    WARNING;
  - marker present → copy only `<tmp>/<spec_dir relative to data_root>/` over
    `spec_dir`, per file via temp sibling + rename, excluding
    `worker_ref.json` and the sentinel/attempt files; never the worktree or git
    repo; sentinel `{"restored": true, job_id, restored_at}`;
  - failure → ERROR log, increment `.workspace_restore_attempts`, no sentinel;
    at `TFACTORY_WORKSPACE_RESTORE_MAX_ATTEMPTS` (default 20) write the
    sentinel `restored: false` with the last error.
- `data_root` = `getattr(sandbox, "data_root", _DEFAULT_DATA_ROOT)` as the
  pack uses (`verify_dispatch.py:1064`, default `/home/nonroot/.tfactory`), so
  pack and restore agree on relative paths.
- **Nothing is deleted** from MinIO; the `workspace` role lifecycle rule
  (#399) bounds retention.
- Enabling the toggle on the cluster and the step-3 observation need the
  operator's explicit go-ahead at that time.

Branch: `feat/1160-pack-toggle-unpack`.

## Steps

1. **Tests first (must fail on dev):**
   - `tests/test_verify_dispatch.py`: toggle unset/`false`/`garbage` →
     `pack_workspace=False` reaches `dispatch_verify_job`, manifest keeps
     `repo_pvc`, `worker_ref.json` has no `workspace_uri`; `true` (with a fake
     store) → `workspace_uri` in both `worker_ref.json` and the durable row.
   - new `tests/test_workspace_restore.py` (fake store, tmp data root with a
     spec dir, a project worktree and a fake git dir):
     terminal + marker → spec subtree restored, sentinel `restored: true`,
     worktree + git dir byte-identical; no marker → nothing restored,
     `status.json` keeps its `stuck` content, sentinel `restored: false`;
     non-terminal row → untouched, no sentinel; fetch error → no sentinel,
     attempts incremented, retried next call; attempts at the cap → sentinel
     `restored: false` with the error; no `workspace_uri` → never fetched;
     sweep never raises (store raising → logged, returns).
   - `tests/test_verify_workspace.py`: the Job writes `.pushed_back.json`
     before `push_back_workspace` packs (order asserted via the fake store's
     captured archive containing the marker).
   → verify by failures on unmodified code.
2. `gen_functional.py`: `_pack_workspace_enabled()` + pass it at `:531`.
   → verify by the toggle tests.
3. `verify_dispatch.py`: after `_pack_workspace_for` returns a URI, re-record
   `worker_ref` (spec file + durable row) with `workspace_uri`.
   → verify by the packed-flag test; existing `test_verify_dispatch.py` green.
4. `verify_pipeline.py`: write the marker into `packed_root`'s spec dir before
   `push_back_workspace`.
   → verify by the marker-order test.
5. `verify_workspace.py`: `restore_spec_from_workspace(spec_dir, job_id, uri,
   data_root) -> bool | None` (extract to temp, marker check, subtree copy
   with per-file rename, sentinel/attempts), using the vendored
   `unpack_workspace`.
   → verify by the restore unit tests.
6. `verify_dispatch.py`: `restore_packed_workspaces_once(store, data_root)`
   (the walk + terminal check) called from `reconcile_and_reap_once` after the
   reconcile pass, inside its existing never-raise guard.
   → verify by the sweep tests and existing reconcile tests green.
7. Mutation checks: drop the marker check → "no push-back" test fails; copy
   the whole archive → untouched-worktree test fails; drop the terminal check
   → non-terminal test fails.
   → verify by observed failures, then restore.
8. Full suites + ratchet (pinned, after commit) + format; confirm
   `tools/runners/artifact_store.py` has no diff (vendored).
   → verify all green and `git diff origin/dev -- apps/backend/tools/runners/artifact_store.py` empty.
9. Commit, push, PR to `dev` linking intent/spec/plan; state in the PR that
   the toggle defaults off and nothing changes until it is set.
   → verify CI green; merge per the user's instruction.
10. **Step 3 of the spec (operator-approved, after a deploy):** set the toggle
    for one real spec; observe the verify Job on a node that does not hold the
    worktree; afterwards the PVC `status.json` matches the durable verdict and
    the evidence tree is present. Record on #1160. Only then write PR B's plan.

## Tests

```bash
apps/backend/.venv/bin/pytest tests/test_verify_dispatch.py tests/test_verify_workspace.py tests/test_workspace_restore.py -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q
PATH=<ratchet-venv>/bin:$PATH python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
git diff origin/dev -- apps/backend/tools/runners/artifact_store.py   # expect empty
```

## Rollback

Revert the commit. With the toggle unset (the default) PR A changes no
runtime behaviour: no dispatch writes `workspace_uri`, so the sweep finds
nothing. If the toggle has been enabled, unset it first — rows already
dispatched packed keep their recorded semantics and are restored by the sweep
until the revert lands.

## Deviations (recorded during implementation, same commit as the code)

- **Step 3 — pack before recording, not re-record after.** `update_status`
  re-maps the lifecycle (`has_verdict` defaults True), so a second write just
  to add `workspace_uri` was a risk. The pack now runs before `worker_ref` is
  built; the first record carries `job_id`, `workspace_uri` and `project_dir`,
  and is still written before the Job is applied (what the orphan reaper
  needs). The co-mounted path's `worker_ref` is byte-identical.
- **The worktree lives inside the spec dir** (`spec_dir/.worktree`, a linked
  worktree with a `.git` pointer the Job may rewrite). "Copy only the spec
  subtree" therefore also skips `project_dir` when it is inside `spec_dir`;
  `project_dir` is recorded in the packed `worker_ref` for this.
- **Marker never restored + job-scoped.** The restore puts the spec dir back on
  the PVC, which the NEXT dispatch packs; a copied marker would then vouch for a
  Job that never pushed back. The marker must name this `job_id`, and it (with
  `worker_ref.json` and the sentinel/attempt files) is never copied back.
- **Sentinel and attempt counter are job-scoped** (`restore_outcome`). They
  live in the spec dir, which outlives the Job; a rerun of the same spec is a
  new Job and must not be skipped by the previous one's sentinel. Test:
  `test_a_rerun_is_not_blocked_by_the_previous_jobs_sentinel` (failed first).
- **Tests live in `tests/test_verify_workspace.py`**, not a new
  `test_workspace_restore.py`: they reuse that module's fake S3, `data_root`
  fixture and real-entrypoint driver, and the repo has no convention for
  importing fixtures across test modules. Added
  `test_reconcile_tick_runs_the_restore_sweep` (wiring) and
  `test_restore_is_idempotent`.
- `reconcile_and_reap_once` gains an optional `data_root` (tests); production
  resolves it from `nix_runner_from_env()` exactly as dispatch does.
- **Follow-up after the local ratchet (second commit):** the strict shared
  ruff bar flagged the sweep doing blocking filesystem I/O on the event loop
  (ASYNC240) — the spec-dir walk now runs in `asyncio.to_thread`
  (`_undecided_packed_specs`), like the restore itself. The
  `max_attempts` parameter was dropped (PLR0913); the limit is the env var
  only, and the test sets it. Plus PTH201/PTH105/E501 cleanups.
- `tests/test_evaluator.py`'s fake `dispatch_verify_job` gained
  `pack_workspace=False` (the call now always passes it) and asserts the
  default is `False` — "unset changes nothing" pinned at the call site too.
- **Post-review fix (promotion PR #1304, Copilot):** the restore's attempt
  limit was parsed with `int(...)` outside the protected block, so a malformed
  `TFACTORY_WORKSPACE_RESTORE_MAX_ATTEMPTS` raised on every tick and the spec
  was never restored. Now `_restore_max_attempts()` falls back to the default
  with a warning (min 1). Test: `test_a_malformed_attempt_limit_does_not_stop_restores`
  (failed first with the `ValueError`).
