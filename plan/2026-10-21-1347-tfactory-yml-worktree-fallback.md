---
status: approved
issue: 1347
spec: spec/2026-10-21-1347-tfactory-yml-worktree-fallback.md
---

# Plan: `.tfactory.yml` worktree fallback + Nix environment marker

## Approved decisions (self-contained)

- Snapshot `context/tfactory_yml.json` first; else parse
  `spec_dir/.worktree/.tfactory.yml` with `load_tfactory_yml` and
  `model_dump(mode="json")`. Any error -> None.
- One helper in `evaluator_targets.py` replaces the three snapshot reads.
- `"Cannot build '/nix/store/"` joins `_ENVIRONMENT_MARKERS`.
- No change to the ingest route or the AIFactory handoff.

## Steps

1. `apps/backend/agents/evaluator_targets.py`: add `_load_tfactory_cfg` and use
   it in `_browser_target_url`, `_resolve_target`, `_test_credential_specs`.
2. `apps/backend/agents/stability_runner.py`: add the Nix marker.
3. Tests: `tests/test_evaluator_targets_worktree_fallback.py` (fallback,
   snapshot precedence, invalid yml -> None) and a case in
   `tests/test_stability_runner.py` for the Nix marker.

## Tests

`pytest tests/test_evaluator_targets_worktree_fallback.py tests/test_stability_runner.py tests/test_evaluator_api_self_serve.py`; ruff check/format.

## Rollback

Revert the PR; no data or config migration.

## Implementation notes

- The coder (agy) could not write files in this lane (its tool hook rejected
  every write), so steps 1-3 were done by the session model. No deviation from
  the steps above.
