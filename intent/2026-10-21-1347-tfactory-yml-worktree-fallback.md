---
status: draft
issue: 1347
related: 1343
author: olafkfreund
---

# Intent: the api lane sees the repo's `.tfactory.yml` on every ingest path

## Problem

The Evaluator resolves targets only from the snapshot
`spec_dir/context/tfactory_yml.json`
(`apps/backend/agents/evaluator_targets.py:41,73,162`). The only writer of that
snapshot is `snapshot_aifactory_spec` (`workspaces/snapshotter.py:242-257`),
called only from `task_control.py:762`. Specs from `POST /api/specs/ingest` and
from the AIFactory handoff never get it, so a valid `.tfactory.yml` that sits in
`spec_dir/.worktree/` is ignored and every api test dies on
`KeyError: 'TFACTORY_TARGET_URL'` (#1347, root cause of #1343).

#1343 also reports Nix environment build failures (`Cannot build
'/nix/store/...-env.drv'`) classified as `assertion`. The `KeyError` half of
that is fixed by #1344/#1348; the Nix half is not.

## Proposed outcome

- A `.tfactory.yml` in the spec's checkout resolves the target for the api,
  browser and credential lookups even when no snapshot was written.
- A Nix "Cannot build" failure is classified `environment`, not `assertion`.

## Affected users and systems

Backend Evaluator target resolution and the stability classifier. No API or
storage change.

## Constraints

- The snapshot stays authoritative when it exists.
- An unparseable `.tfactory.yml` must not crash the Evaluator (same as a
  missing one).

## Open questions

None.
