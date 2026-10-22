---
status: draft
issue: 1400
spec: spec/2026-10-22-1400-repo-owned-flake-tracked.md
---

# Plan: a flake is repo-owned only when git tracks it

Approved decisions: a `flake.nix` is repo-owned only if git tracks it (`git ls-files
--error-unmatch`, `-c safe.directory=*`); non-zero exit = not owned; git unable to run =
warn + fall back to existence. `materialize_flake(owner_dir=)` lets the pytest scratch
ask the source checkout. Log origin + sha256 on the repo-owned lines. Lanes that
materialise in the shared checkout delete a generated flake/lock in their `finally`.
`evaluator._nix_verify_mode` uses the same helper. Not touching #1397 or the
stale-branch checkout. Whole-lane scratch copies for non-pytest lanes rejected (see spec).

## Steps

1. `apps/backend/agents/nix_env.py` near `materialize_flake` (~2072): add
   `flake_is_repo_owned` + `_drop_generated_flake(plan)`; add `owner_dir` kwarg; replace both
   existence checks; log origin. → `pytest tests/test_nix_env.py`
   Traps: `subprocess` must not raise on missing git; mypy --strict ratchet.
2. Same file, lanes: pytest (~654) pass `owner_dir=src`; go (~961), gradle (~1184),
   maven (~1286), jest (~1417), browser (~1851) call `_drop_generated_flake(plan)` in the
   `finally` after `sandbox.run`. → `pytest tests/test_jest_lane_*.py tests/test_nix_env.py`
   Traps: go/gradle/maven may collect results after the finally; only flake files are removed.
3. `apps/backend/agents/evaluator.py` ~313: `_nix_verify_mode` uses `flake_is_repo_owned`.
   → `pytest tests/test_jest_lane_batched_reruns.py tests/test_evaluator.py`
4. Tests: update tests that wrote flakes into non-git dirs to commit them; add the
   regression tests listed in the spec. → `pytest tests` targeted + ratchet.

## Tests

`cd apps/backend && .venv/bin/python -m pytest ../../tests/test_nix_env.py ../../tests/test_jest_lane_*.py ../../tests/test_evaluator*.py -q` → all pass.
`python scripts/ratchet_lint.py --base origin/dev` → pass.

## Rollback

Revert the code commit; artifacts stay.
