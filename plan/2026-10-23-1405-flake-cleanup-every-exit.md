---
status: approved
issue: 1405
spec: spec/2026-10-23-1405-flake-cleanup-every-exit.md
---

# Plan: Remove a generated flake on every lane exit path

Decisions (from the approved spec): sandbox check before `materialize_flake`;
the existing `try/finally` widened to start right after the plan exists; the
`finally`'s `_drop_generated_flake` (skips tracked files) does the removal;
no new helper; pytest lane untouched.

## Steps

1. `apps/backend/agents/nix_env.py` go, gradle, maven lanes: swap the sandbox
   check above `materialize_flake`; wrap module-dir lookup, staging and
   `sandbox.run` in the existing `try`. → `pytest tests/test_nix_env.py`
   Traps: keep the diff to these functions (concurrent `repo_pvc` change).
2. Same file, jest lane: same reorder; hoist `staged = None` above the
   `try`. Browser lane: same reorder; the zero-spec return moves inside the
   `try` and loses its own drop call. → `pytest tests/test_jest_lane_*.py
   tests/test_nix_env.py`
3. `tests/test_nix_env.py`: parametrised test over the five lanes with
   `TFACTORY_NIX_RUNNER_IMAGE` unset: `git status --porcelain` clean after
   return; a raising module-dir lookup still removes the flake; a tracked
   flake survives. → the new tests fail on dev, pass with steps 1-2.

## Tests

`cd apps/backend && .venv/bin/pytest tests/test_nix_env.py tests/test_jest_lane_*.py
tests/test_evaluator_go_lane.py -q`, ruff, `scripts/ratchet_lint.py`.

## Rollback

Revert the PR; behaviour returns to #1403 (flake left only on early exits).
