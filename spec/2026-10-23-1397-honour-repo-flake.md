---
status: draft
issue: 1397
intent: intent/2026-10-23-1397-honour-repo-flake.md
---

# Spec: one nix-mode predicate that honours a repo-owned flake

## Design

Chosen (the user's decision): **honour the repo flake**.

1. New `nix_env.has_nix_env(spec_dir, project_dir, owner_dir=None) -> bool`:
   `is_nix_environment(environment_from_contract(spec_dir))` OR
   (`project_dir is not None` AND `flake_is_repo_owned(project_dir, owner_dir)`).
   `flake_is_repo_owned` (#1400/#1403) only accepts a git-tracked flake, so an
   untracked or generated one is never honoured. This is the single source of truth.
2. `evaluator._nix_verify_mode` keeps its env-var overrides (`TFACTORY_VERIFY_BACKEND`,
   `TFACTORY_NIX_RUNNER_IMAGE`) and then returns `has_nix_env(spec_dir, project_dir)`
   inside its existing try/except (a contract-read error still means legacy runner).
3. `nix_env.run_pytest_lane_via_nix` replaces its `if not is_nix_environment(env)`
   gate with `if not has_nix_env(spec_dir, project_dir)`. The rest already works:
   `materialize_flake(…, owner_dir=src)` on the scratch copy takes its
   "honouring repo-owned flake" branch and returns a non-generated plan, so the Job runs
   `nix develop path:/work#default` in the repo's devShell, packed like any other lane.
4. Other lanes need no change: go/kotlin/java synthesize a nix env when the contract has
   none, and jest/browser/deploy go straight to `materialize_flake`, which already
   honours a repo-owned flake. `run_pytest_lane_via_nix` was the only hard gate (grep
   `is_nix_environment` in `nix_env.py`).
5. A repo devShell without python exits 127; #1401 already grades that not_run /
   environment. Unchanged: never a reject, never a pass.

## Alternatives rejected

- **Evaluator stops claiming nix mode for repo-flake-only runs** (revert #1187's half):
  would make the predicates agree, but contract-less runs would lose nix verify and the
  batched paths (#776, #1188) again, and packed mode (#1160) would stay unexercised.
  The user chose to honour the flake instead.
- **Keep two predicates and fix only nix_env**: agrees today, drifts next time.

## Risks

- Contract-less repos whose committed flake has no Python (e.g. a Node-only fixture):
  the pytest lane now runs in that devShell and gets exit 127, graded not_run /
  environment (#1401) instead of falling back to host/docker. Intended by the decision.
- One extra `git ls-files` per pytest lane: milliseconds.

## Verification

- Tests: contract-less + tracked flake: `has_nix_env` and `_nix_verify_mode` true, and
  `run_pytest_lane_via_nix` dispatches a Job (fake sandbox). Contract-less + untracked
  flake: both false, no Job. Contract-declared nix env: unchanged (existing tests).
- `pytest tests/test_nix_env*.py tests/test_evaluator*.py`, ruff,
  `scripts/ratchet_lint.py --base origin/dev`.
