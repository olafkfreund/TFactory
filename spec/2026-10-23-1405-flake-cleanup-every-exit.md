---
status: draft
issue: 1405
intent: intent/2026-10-23-1405-flake-cleanup-every-exit.md
---

# Spec: Remove a generated flake on every lane exit path

## Design

In each of `run_gotest_lane_via_nix`, `run_gradle_lane_via_nix`,
`run_maven_lane_via_nix`, `run_jest_lane_via_nix` and `run_browser_evidence`:

1. Check `nix_runner_from_env()` first, so the no-image return happens before
   anything is written.
2. Then call `materialize_flake`; return `None` if it yields no plan (nothing
   written that needs removing).
3. Widen the existing `try` so it starts right after the plan exists and
   covers module-dir lookup, staging and the Job run. The existing `finally`
   (which calls `_drop_generated_flake`, which already skips tracked files)
   then runs on every exit path.

The browser lane's `no specs` return moves inside the `try`; its explicit
`_drop_generated_flake` call goes, as the `finally` does it. The jest lane's
`staged` is initialised before the `try` so the `finally` can read it.
The pytest lane works on a scratch copy that is removed already; untouched.

No new helper: the lanes already share `_drop_generated_flake`.

## Alternatives rejected

- Move `materialize_flake` down to just before the Job run: on a `None`
  plan the lane would have to undo its staging, adding cleanup code.
- A context-manager helper: the same indentation change plus a new
  abstraction for five call sites that already share a cleanup function.

## Risks

Re-indented blocks could conflict with the concurrent `repo_pvc` removal;
that change lives elsewhere in the file. Browser lane with zero specs now
also removes the empty stage dir (harmless, cleaner).

## Verification

New tests in `tests/test_nix_env.py`: with no runner image, each lane leaves
`git status --porcelain` clean; a lookup that raises still removes the
flake; a tracked flake survives. Existing lane tests, ruff, mypy ratchet pass.
