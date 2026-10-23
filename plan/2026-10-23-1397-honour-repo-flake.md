---
status: approved
issue: 1397
spec: spec/2026-10-23-1397-honour-repo-flake.md
---

# Plan: one nix-mode predicate that honours a repo-owned flake

Approved decisions (from the spec):

- User decision: honour the repo flake. One shared predicate
  `nix_env.has_nix_env(spec_dir, project_dir, owner_dir=None)` = contract nix env OR a
  git-tracked flake (`flake_is_repo_owned`, #1400). Untracked/generated flakes never count.
- `evaluator._nix_verify_mode` and `nix_env.run_pytest_lane_via_nix` both use it.
- Other lanes unchanged: go/kotlin/java synthesize an env; jest/browser/deploy already
  call `materialize_flake`, which honours a repo-owned flake.
- Exit 127 in a devShell without the tool stays not_run/environment (#1401).
- Rejected: evaluator stops claiming nix mode for repo-flake-only runs.

## Steps

1. `apps/backend/agents/nix_env.py`, after `flake_is_repo_owned` (~line 2125): add
   `has_nix_env(spec_dir: Path, project_dir: Path | None, owner_dir: Path | None = None) -> bool`.
   In `run_pytest_lane_via_nix` (~line 605) replace `if not is_nix_environment(env): return None`
   with `if not has_nix_env(spec_dir, project_dir): return None`; keep `env` and pass it on
   to `materialize_flake` (None for a contract-less spec → repo-flake branch).
   → verify by `pytest tests/test_nix_env.py -q`.
   Traps: mypy --strict ratchet; PLR0911 on extra returns; `has_nix_env` must not
   raise on a missing contract (`environment_from_contract` returns None).
2. `apps/backend/agents/evaluator.py:_nix_verify_mode` (~lines 298-314): replace the
   contract check + repo-flake return with `return has_nix_env(spec_dir, project_dir)`
   inside the existing try/except; import it from `agents.nix_env`; trim the comment.
   → verify by `pytest tests/test_evaluator.py -q -k nix_verify_mode`.
   Traps: keep env-var precedence; an exception still returns False.
3. Tests: `tests/test_nix_env.py`: contract-less spec + committed flake (`_commit_flake`)
   → `has_nix_env` true and `run_pytest_lane_via_nix` dispatches (fake sandbox, command
   starts `nix develop path:/work#default`); same with an untracked flake (git init, not
   added) → `has_nix_env` false and lane returns None with no sandbox call.
   `tests/test_evaluator.py`: `_nix_verify_mode` true for tracked, false for untracked,
   with the image env set and no contract env.
   → verify by both test files passing; the new dispatch test fails on origin/dev.

## Tests

`cd apps/backend && .venv/bin/pytest ../../tests/test_nix_env.py ../../tests/test_evaluator.py -q`
(or the repo's pytest invocation), `ruff check`, `python scripts/ratchet_lint.py --base origin/dev`.

## Rollback

Revert the PR; behaviour returns to the disagreeing predicates.
