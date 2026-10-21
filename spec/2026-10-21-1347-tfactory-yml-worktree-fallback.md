---
status: draft
issue: 1347
intent: intent/2026-10-21-1347-tfactory-yml-worktree-fallback.md
---

# Spec: fall back to `.worktree/.tfactory.yml` when the snapshot is missing

## Design

- One private reader `_load_tfactory_cfg(spec_dir) -> dict | None` in
  `agents/evaluator_targets.py`: return the parsed snapshot
  `context/tfactory_yml.json` when it exists; otherwise
  `load_tfactory_yml(spec_dir / ".worktree")` dumped with
  `model_dump(mode="json")` (the same shape the snapshotter writes). Any
  read, JSON or `TFactoryYmlError` returns None.
- `_browser_target_url`, `_resolve_target` and `_test_credential_specs` use it
  instead of their three copies of the snapshot read.
- `agents/stability_runner.py`: add `"Cannot build '/nix/store/"` to
  `_ENVIRONMENT_MARKERS`.

## Alternatives rejected

- Calling the snapshotter from the ingest route and the AIFactory handoff:
  two call sites in other modules and the snapshotter needs inputs those paths
  do not all have. The fallback covers both paths, and any future one, in one
  place.

## Risks

- A repo checkout with an invalid `.tfactory.yml` silently yields no target,
  as it did before (the #1344 guard then reports the lane honestly).

## Verification

- Unit test: spec_dir with only `.worktree/.tfactory.yml` (http target) ->
  `_browser_target_url` returns its base_url; fails on dev.
- Unit test: snapshot wins over the worktree file.
- Unit test: `classify_pytest_failure` on a Nix "Cannot build" output ->
  `environment`; fails on dev.
- ruff + the touched test files.
