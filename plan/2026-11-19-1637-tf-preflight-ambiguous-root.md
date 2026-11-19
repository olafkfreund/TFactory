---
status: approved
issue: 1637
spec: spec/2026-11-19-1637-tf-preflight-ambiguous-root.md
reviewers:
  - codex  # independent read-only diff review
---

# Plan: Retry the probe against every root of an ambiguous package

Decisions from the approved spec: retry the probe with each other package root first on exit 2; reject only if all copies lack the name; execution-time roots changed in step 3 (deviation).

## Steps

1. apps/backend/agents/preflight_static.py `check_import`: extract `_probe(order)`, add the retry loop on exit 2 with several roots → verify by `pytest tests/test_preflight_static.py`
   Traps: keep cwd=project_dir and the #732 root ordering.
2. tests/test_preflight_static.py: add the two-`app` tests → must fail on the old code.
3. (Deviation, added after approval at the coordinator's request; live evidence: unit test `from app.main import app` hit src/app in the verify Job.) `package_root_rel_paths(root, targets)` orders the root holding a spec target first; `nix_env._in_job_pythonpath` and `evaluator._run_pytest_on_host` pass `_api_targets(spec_dir, ...)` (the #1633 helper, reused); no targets keeps the old order. Evaluator now prepends so the first entry wins, consistent with nix_env.

Implemented by the lead (two small steps, below the handoff threshold).

## Tests

`pytest tests/test_preflight_static.py -q` passes; the accept test fails on `origin/dev`.

## Rollback

Revert the commit.
