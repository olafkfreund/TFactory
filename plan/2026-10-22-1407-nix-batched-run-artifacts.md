---
status: draft
issue: 1407
spec: spec/2026-10-22-1407-nix-batched-run-artifacts.md
---

# Plan: persist Nix batched lane junit and coverage

Approved decisions:

- This is a pre-existing gap, not a packing regression. Pack/collect already returns
  `.tf_pytest/`, and `run_pytest_lane_via_nix` returns the report paths in both modes.
- The two Nix batched evaluator functions drop those paths. The fix routes them through
  the existing `_with_durable_artifacts` into `findings/_run_artifacts/<stem>/`.
- Nothing is fabricated. A missing report stays `None`, and coverage stays unmeasured.

## Steps

1. `apps/backend/agents/evaluator.py`, in `_nix_batched_stability` (pytest branch,
   after `if res is None: return None`) and in `_nix_batched_signals` (after
   `if res is None: return None, None`): add
   `_with_durable_artifacts(spec_dir, test_file, res)`.
   Verify with `pytest tests/test_evaluator.py -k nix_batched`.
   Traps: the mypy ratchet `scripts/ratchet_lint.py --base origin/dev`.
2. `tests/test_nix_batched_run_artifacts.py`: add unpacked, packed round-trip and
   missing-report tests that drive the real `run_pytest_lane_via_nix` with a fake
   sandbox.
   Verify with `pytest tests/test_nix_batched_run_artifacts.py`. The tests must fail
   with step 1 reverted.
   Traps: monkeypatch `environment_from_contract`, `materialize_flake` and
   `nix_runner_from_env` in `agents.nix_env`.

## Tests

`pytest tests/test_nix_batched_run_artifacts.py tests/test_evaluator.py` passes.
The ratchet passes.

## Rollback

Revert the PR.
