---
status: approved
issue: 1400
author: olafkfreund
---

# Intent: a flake is repo-owned only when git tracks it

## Problem

`nix_env.materialize_flake` decides "repo-owned" by `flake.nix` existing. The jest and
browser lanes write generated fallback flakes into the shared project checkout and leave
them there. The next lane then reads TFactory's own Node-only output as the repo's
committed flake, logs `respecting repo-owned flake.nix`, and the pytest lane exits 127
(`python: command not found`). Confirmed on the live checkout: `?? flake.nix`, untracked,
GENERATED header, no python. A leftover untracked flake also blocks a later checkout of
a tracked one.

## Proposed outcome

- A `flake.nix` counts as repo-owned only when git tracks it in the project checkout.
- Generated flakes never stay in the shared checkout after a lane: its `git status` is clean.
- The `respecting repo-owned` log line says where the flake came from.

## Affected users and systems

TFactory backend: `agents/nix_env.py` (all nix lanes), `agents/evaluator.py`
(`_nix_verify_mode`). Every nix-verified run.

## Constraints

- A committed flake must still win, including in the pytest lane's `.git`-less scratch copy.
- Do not change the #1397 predicate question (which predicate is the source of truth).
- Do not change the stale-branch checkout seen during spec ingest (separate concern).
- Mypy ratchet must pass.

## Open questions

None. The user chose: tracked-by-git ownership, and no generated flakes left in the checkout.
