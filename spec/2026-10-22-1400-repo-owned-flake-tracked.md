---
status: approved
issue: 1400
intent: intent/2026-10-22-1400-repo-owned-flake-tracked.md
---

# Spec: a flake is repo-owned only when git tracks it

## Design

1. New helper `nix_env.flake_is_repo_owned(project_dir, owner_dir=None) -> bool`:
   `flake.nix` exists in `project_dir` AND
   `git -c safe.directory=* -C <owner_dir or project_dir> ls-files --error-unmatch -- flake.nix`
   exits 0. Any non-zero exit (untracked, ignored, not a git repo) means not repo-owned.
   If git cannot run at all (binary missing, timeout) it logs a warning and falls back
   to existence: never overwrite what might be a committed flake on a host we cannot
   inspect.
2. `materialize_flake` gains `owner_dir: Path | None = None` and uses the helper in both
   places it keyed on existence (the no-contract-env branch and `repo_has_flake`).
   The pytest lane passes `owner_dir=src` because its scratch copy has no `.git`.
   An untracked flake is regenerated from the current manifest, like a missing one.
3. The `respecting repo-owned` and `honouring repo-owned` lines log the origin:
   `origin=git-tracked sha256=<first 12>`. The write line says `origin=generated`.
4. Lanes that materialise into the shared checkout (go, gradle, maven, jest, browser)
   remove `flake.nix` and `flake.lock` in their existing `finally` when
   `plan.generated`, exactly like the job script they already clean up. After a lane,
   the checkout's `git status` is clean.
5. `evaluator._nix_verify_mode` uses the helper instead of `is_file()`, so a stale
   untracked flake no longer switches nix mode on. Which predicate is the source of
   truth (#1397) is unchanged.

## Alternatives rejected

- **Discriminate by the GENERATED header**: the committed fixture flake carries the same
  RFC-0005 header.
- **Move the jest/browser/go/gradle/maven lanes onto per-run scratch copies (#623 shape)**:
  this is what the decision literally asked for. Rejected for this PR: those lanes also
  write their job script, jest config, staged specs and `node_modules` into the checkout,
  so moving only the flake buys no isolation, and moving the whole lane changes npm
  caching and screenshot collection. The user-visible goal (no generated flake survives
  in the checkout, a stale one is never honoured) is met by step 4. The pytest lane
  already runs on a scratch copy.
- **Treat "git unavailable" as not repo-owned**: would overwrite a committed flake on a
  host whose git is broken. Fallback to existence plus a warning is safer and explicit.

## Risks

- A repo that ships `flake.nix` untracked (no commit) is no longer honoured: intended.
- Tests that wrote a flake into a non-git tmp dir and expected it honoured must commit it.
- One extra `git ls-files` per lane: milliseconds.

## Verification

- New tests: untracked generated flake is not honoured (regenerated, origin=generated);
  a committed flake is honoured; pytest-lane scratch honours a flake tracked in the
  source checkout; jest lane leaves `git status --porcelain` empty; evaluator nix mode
  ignores an untracked flake.
- `pytest tests/test_nix_env.py tests/test_jest_lane_* tests/test_evaluator*.py`,
  ruff, `scripts/ratchet_lint.py --base origin/dev`.

## Out of scope

- #1397 predicate choice.
- Spec ingest verifying a checkout pinned to an old task branch.
