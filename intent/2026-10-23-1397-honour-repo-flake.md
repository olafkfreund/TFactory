---
status: draft
issue: 1397
author: olafkfreund
---

# Intent: one nix-mode predicate that honours a repo-owned flake

## Problem

On a contract-less run (every spec ingest, every skip_planning card) the two
"run verify under Nix?" checks disagree. `evaluator._nix_verify_mode` says yes when the
checkout carries a git-tracked `flake.nix` (#1187). `nix_env.run_pytest_lane_via_nix`
only accepts a contract-declared nix env and returns None. The run logs
`nixjob verify unavailable … falling back to host/docker`, no nested lane Job is
dispatched, and packed mode (#1160) is never exercised on that path.

## Proposed outcome

- Both checks come from one shared predicate, so they cannot drift again.
- A contract-less run whose checkout has a git-tracked flake dispatches its pytest lane
  as a nix Job in the repo's devShell.
- An untracked or generated flake is never honoured.
- A devShell that lacks the tool still grades exit 127 as not_run/environment (#1401).

## Affected users and systems

TFactory backend: `agents/nix_env.py`, `agents/evaluator.py`. Contract-less runs on
repos that commit a flake.

## Constraints

- Contract-declared nix env behaviour unchanged.
- Never reject or pass on a devShell missing the tool; keep #1401 grading.
- Mypy ratchet must pass.

## Open questions

None. The user chose: honour the repo flake.
