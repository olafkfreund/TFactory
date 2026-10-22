---
status: approved
issue: 1407
author: olafkfreund
---

# Intent: Nix batched pytest lanes never keep junit and coverage

## Problem

The packed run `postdeploy-1022c-packed-nix` logged `coverage measured=0 unmeasured=2`
and left no `findings/_run_artifacts/` or `findings/runs/`. The Nix lane does write
`junit.xml` and `coverage.xml` and `run_pytest_lane_via_nix` returns their paths, but
`_nix_batched_signals` and `_nix_batched_stability` (the path every Python subtask takes
in Nix mode) only read `res.stdout` and drop the paths. `_measured_coverage` therefore
finds nothing.

The issue asks whether this is a packing regression. It is not: the unpacked path drops
the files at the same two call sites. Packing returns the whole `/work` tree, including
`.tf_pytest/`, to the scratch dir, so both modes reach the same drop point.

## Proposed outcome

A Nix batched pytest lane, packed and unpacked, leaves `junit.xml` and `coverage.xml` in
`findings/_run_artifacts/<stem>/`, and the verdict's coverage is measured.

## Affected users and systems

TFactory verify (`apps/backend/agents/evaluator.py`). Every Nix-mode Python subtask.

## Constraints

- Nothing may fail open. A lane with no coverage report stays `unmeasured` and gets no
  made-up number.
- The unpacked path must not regress, and the packing toggle (#1160) is not blocked.

## Open questions

None.
