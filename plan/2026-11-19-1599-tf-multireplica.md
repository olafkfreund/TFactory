---
status: draft
issue: 1599
spec: spec/2026-11-19-1599-tf-multireplica.md
coders:
  2: codex   # contained, tricky reconcile/heartbeat/wait logic in one file
  4: codex   # tests for step 2, written against the spec's interleavings
reviewers:
  - codex    # independent read-only review of each draft and the final diff
---

# Plan: owner-aware inline-stage reconciler (#1599)

Approved spec decisions, carried over: per-process `INSTANCE_ID`; `write_status_patch` stamps `owner_instance` when status is `planning`/`generating`; per-instance heartbeat file `<workspace_root>/instances/<id>` (beat 15 s, stale 60 s); `reconcile_inline_orphans` skips own and live-owner stages, fails stale/missing-owner ones as `control_plane_restart`, fails ownerless ones only when `updated_at` is older than 900 s, re-reads and compares before writing, and also runs every heartbeat tick; first heartbeat written synchronously before any reconcile and before yield; `reconcile_interrupted_builds` runs at startup only when no other live instance exists; shutdown waits up to 600 s for own planning/generating stages then fails leftovers and removes the heartbeat; factory-gitops sets `terminationGracePeriodSeconds: 900` (separate PR). No Postgres lease, no hand-back to queued.

## Steps

1. `apps/backend/agents/workspace_status.py` (top-level constants, `write_status_patch` ~l.83): add `INSTANCE_ID` and `INLINE_STATUSES = {"planning","generating"}`; in `write_status_patch`, `if fields.get("status") in INLINE_STATUSES: fields = {**fields, "owner_instance": INSTANCE_ID}`. Add both to `__all__`. Check: `pytest tests/test_liveness_sweep.py -k owner`. (coder: me)
2. `apps/backend/agents/liveness_sweep.py` (coder: codex): add `instance_dir(root)`, `beat(root, instance_id=INSTANCE_ID)` (mkdir + touch), `instance_alive(root, id, *, now, stale)`, `other_instance_alive(root, *, now, stale)`, `purge_old_instances(root, now)` (files older than 1 day), `drain_own_inline_stages(root, *, timeout, poll=2.0, instance_id)` (sync, waits, then fails leftovers with `phase=control_plane_restart`, reason "shut down mid-<stage> (#1599)", then unlinks own heartbeat). Change `reconcile_inline_orphans(root, now, *, instance_id=INSTANCE_ID, stale_seconds, legacy_seconds)` per the spec incl. compare-and-write; env defaults `TFACTORY_INSTANCE_STALE_SECONDS=60`, `TFACTORY_LEGACY_ORPHAN_SECONDS=900`. Keep the existing return shape and all other functions untouched. Check: step 4 tests.
3. `apps/web-server/server/config.py`, `apps/web-server/server/background/instance_heartbeat.py` (new, small loop: beat, then `to_thread(reconcile_inline_orphans)` when enabled, then `purge_old_instances`, sleep), `apps/web-server/server/main.py` lifespan (coder: me): synchronous first `beat` before the #774 reconcile; start the loop task; gate the #1368 block on `not other_instance_alive`; in shutdown, before cancelling tasks, `await asyncio.to_thread(drain_own_inline_stages, ..., timeout=settings.SHUTDOWN_DRAIN_SECONDS)` then cancel the loop. Settings: `INSTANCE_HEARTBEAT_SECONDS=15`, `SHUTDOWN_DRAIN_SECONDS=600`. Check: `pytest tests/test_terminal_completion_wiring.py tests/test_liveness_sweep.py`.
4. `tests/test_liveness_sweep.py` (coder: codex): add tests that fail without the fix: B leaves A's live stage; A stale/missing heartbeat is failed; legacy ownerless fresh skipped, stale failed; compare-and-write aborts when owner changes; own stage never failed; `write_status_patch` stamps for `planning` only; drain returns early / fails leftovers after timeout and removes heartbeat; `other_instance_alive`. Update any existing test that relied on the old unconditional behaviour. Check: `pytest tests/test_liveness_sweep.py`.
5. Full check (me): `scripts/ratchet_lint.py` for apps/backend and apps/web-server, `ruff check`, the touched tests. Commit with `--no-verify` if the pre-commit hook trips on the unrelated mcp-library collection error in `test_mcp_remote_session_key.py`, and say so in the body.
6. factory-gitops (separate PR, me): `terminationGracePeriodSeconds: 900` on the tfactory Deployment pod spec; replica cap stays 1 (coordinator raises it after release).

Traps: ratchet lint on both apps; ruff `as`-alias rule not relevant here; do not touch `verify_dispatch` (the reaper fix is #1605).

## Tests

`pytest tests/test_liveness_sweep.py tests/test_terminal_completion_wiring.py` green; new tests fail on `dev` without steps 1-3 (stash-free: run them in the dev worktree copy by reverting the source files with `git checkout origin/dev -- <src>`, then restore).

## Rollback

Revert the PR. Leftover `instances/` files are inert. The gitops grace period is independent and harmless.
