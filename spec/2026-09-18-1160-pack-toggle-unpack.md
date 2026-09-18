---
status: approved
issue: 1160
intent: intent/2026-09-18-1160-pack-toggle-unpack.md
---

# Spec: Packed verify workspaces — control-plane restore, toggle, then drop the PVC

Decisions carried from intent review: two PRs under this spec — **PR A**
(restore + toggle, default off) and **PR B** (`repo_pvc` removal, only after
the step-3 observation); #1159 closed into this (done). MinIO retention: not
answered — this spec deletes nothing (see Design §5).

## Findings that change the design proposed on the issue

The #1243-review comment on #1160 proposed: unpack in
`reconcile_and_reap_once` "before that `continue`", idempotent via a
done-marker, retried by the next 15 s tick. Reading the code, three parts of
that do not hold:

1. **The loop never revisits a terminal row.** `reconcile_and_reap_once`
   (`verify_dispatch.py:1449`) iterates `s.recover_in_flight()` — *active*
   (queued/running) rows only (`job_state_store.py:303`). A packed Job writes
   its own terminal row; from then on no tick returns it. The unpack would run
   only in the rare race where the row flips between listing and reconcile,
   and the "retry next tick" can never happen. The store has no terminal-row
   query.
2. **Dispatch and push-back share one object key.** `_workspace_ref`
   (`verify_workspace.py:82`) is the same key for the dispatch pack and the
   Job's push-back. If a Job dies before pushing back, the object still holds
   the **pre-dispatch** workspace; restoring it would overwrite newer PVC
   state — including the reaper's own `stuck` write to `status.json`
   (`verify_dispatch.py:1345`). The durable row cannot disambiguate either: a
   run that pushed back fine but produced no verdict is also `stuck` (#464).
3. **The archive is wider than the spec.** `_pack_workspace_for` packs
   `[spec_dir, project_dir, _git_main_repo(project_dir)]`
   (`verify_workspace.py`, `_relative_roots`). Unpacking the whole archive
   onto the PVC would overwrite the project worktree and the **shared main git
   repo** with one Job's copy.

Also: `tools/runners/artifact_store.py` is vendored from the Factory hub
canonical (drift-gated) — the design must not modify it. And the
`worker_ref` is recorded **before** the pack runs (`dispatch_verify_job`,
`:913` vs `:952`), so it does not carry `workspace_uri` today.

## Design — PR A (restore + toggle, default off)

1. **Toggle.** `TFACTORY_PACK_WORKSPACE`: `true`/`1`/`yes` → pack; anything
   else or **unset** → today's co-mount. Read at the single call site
   (`gen_functional._run_dispatch_blocking` → `dispatch_verify_job(...,
   pack_workspace=...)`, `gen_functional.py:531`), matching the note at
   `verify_dispatch.py:949`. Packing stays fail-open (no store → co-mount).
2. **Record the packed flag where the sweep can find it.** After a successful
   pack, `dispatch_verify_job` re-records the spec's `worker_ref.json`
   (`SPEC_WORKER_REF_FILE`, `_record_spec_worker_ref`) and the durable row's
   `worker_ref` with `workspace_uri` added. Its presence is the packed flag —
   never inferred from the current toggle, so a row dispatched before a flip
   keeps its semantics.
3. **Push-back marker (Job side, TFactory code).** Immediately before
   `push_back_workspace` packs, the Job writes
   `<spec_dir>/.pushed_back.json` (`{job_id, pushed_at}`) inside the root it
   packs. Its presence in the fetched archive is the only proof the object is
   the Job's result and not the pre-dispatch pack. No key change, no vendored
   change, no clock comparison.
4. **Control-plane restore sweep** — a second pass in
   `reconcile_and_reap_once` (same tick, same store), independent of
   `recover_in_flight`:
   - Walk `<data_root>/workspaces/*/specs/*/worker_ref.json`; select those with
     `workspace_uri` and no `<spec_dir>/.workspace_restored` sentinel.
   - `s.get(job_id)`; skip unless the row is terminal.
   - Fetch + extract the object into a temp dir (via the vendored
     `unpack_workspace`, unchanged).
   - **No `.pushed_back.json` for this job_id in the archive** → the Job never
     pushed back: restore nothing, write the sentinel with
     `{"restored": false, "reason": "no push-back"}`, log at WARNING. The PVC
     keeps the reaper's `stuck` state. (This is the #1245 lost-workspace case,
     treated as expected, not retried forever.)
   - Marker present → copy **only the spec-dir subtree**
     (`<tmp>/<rel spec_dir>/`) over `spec_dir`, excluding `worker_ref.json`
     and the sentinel; never the project worktree or the git repo. Then write
     the sentinel `{"restored": true, ...}`.
   - Fetch/extract/copy failure → log at ERROR, **no sentinel**, so the next
     tick retries (the sweep keeps finding it). Bounded by a per-spec attempt
     counter in a `.workspace_restore_attempts` file; after
     `TFACTORY_WORKSPACE_RESTORE_MAX_ATTEMPTS` (default 20, ≈5 min at 15 s) the
     sentinel is written with `restored: false` + the last error, so a
     permanently broken object is visible, not retried forever.
   - The copy writes to a temp sibling and renames per file, so a reader never
     sees a half-written `status.json`.
5. **Retention.** Nothing is deleted. Workspace objects are written with the
   governed `workspace` role tag (`artifact_store.pack_workspace`, #399), so
   the existing role lifecycle rule expires them. Choosing a retention period
   is a lifecycle-rule change in the object store config, out of scope here.

## Design — PR B (after the step-3 observation only)

Remove `repo_pvc` from the verify `kube_sandbox` manifest path and the
co-mount branch; `TFACTORY_PACK_WORKSPACE` then defaults on (or is removed).
Specified in detail in PR B's own plan revision once step 3 has been observed;
not started before.

## Alternatives rejected

- **Unpack at the `continue` in the reconcile loop** (the issue's proposal) —
  unreachable for terminal rows (Finding 1).
- **New store query for recent terminal packed rows** — a DB query + index +
  migration for what a filesystem walk of spec dirs already answers; the
  liveness sweep uses the same walk.
- **Separate object key for the push-back** — cleaner in principle, but the
  key layout lives in the vendored `artifact_store` / canonical conventions;
  the in-archive marker gets the same certainty without touching them.
- **Compare object LastModified with the dispatch time** — clock skew between
  MinIO and the control plane; the marker is exact.
- **Restore the whole archive** — overwrites the shared git repo and worktree
  (Finding 3).
- **Delete the object after restore** — irreversible, and the retention call
  was left open; the lifecycle rule already bounds it.

## Risks

- **Unset toggle changes nothing:** PR A adds a sweep that only acts on
  worker refs carrying `workspace_uri`, which no dispatch writes while the
  toggle is off. Tested explicitly.
- **Sweep cost:** one glob over spec dirs per 15 s tick — the liveness sweep
  already does the same walk.
- **Concurrent writers to the spec dir** during restore (e.g. a reaper write)
  — the restore only runs on terminal rows, after the reaper; per-file rename
  keeps reads consistent.
- **Cluster:** turning the toggle on (factory-gitops) and the step-3
  observation need the operator's go-ahead at that time.
- **Job killed mid-push-back:** handled, not a gap. The marker lives inside
  the archive, and the upload is a single `put_bytes` (S3 never exposes a
  partial object), so an interrupted push-back leaves the pre-dispatch object
  — which has no marker — and the sweep correctly treats it as "no
  push-back".

## Verification

- **Toggle:** unset/`false` → `dispatch_verify_job` called with
  `pack_workspace=False`, manifest has `repo_pvc`, no `workspace_uri` in
  `worker_ref.json`; `true` → packed and recorded.
- **Restore sweep (fake store + tmp data root):** terminal row + marker →
  spec subtree restored, sentinel `restored: true`, project worktree and git
  dir untouched (asserted byte-for-byte); no marker → nothing restored,
  sentinel `restored: false`, PVC `status.json` keeps the reaper's `stuck`;
  non-terminal row → untouched; fetch failure → no sentinel, retried next
  tick, capped by the attempt limit; no `workspace_uri` → never touched.
- **Mutation checks:** drop the marker check → the "no push-back" test fails;
  restore the whole archive → the untouched-worktree test fails; drop the
  terminal check → the non-terminal test fails.
- Suites + ratchet (pinned ruff/mypy) green.
- **Step 3 (operator-approved cluster run, deciding evidence):** toggle on,
  one real spec; the verify Job observed on a node that does **not** hold the
  worktree; afterwards the PVC `status.json` matches the durable verdict and
  the evidence tree is present. Only after that: PR B.
