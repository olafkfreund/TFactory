---
status: draft
issue: 1405
author: olafkfreund
---

# Intent: Remove a generated flake on every lane exit path

## Problem

The go, gradle, maven, jest and browser lanes in `apps/backend/agents/nix_env.py`
call `materialize_flake` before the "no runner image" early return and before
their `try`. The `finally` that removes a generated, untracked `flake.nix` /
`flake.lock` (#1403) therefore never runs when `TFACTORY_NIX_RUNNER_IMAGE` is
unset, or when module-dir lookup or staging raises. The generated flake stays
in the shared checkout.

## Proposed outcome

After any of these lanes returns or raises, the checkout's `git status
--porcelain` shows no generated flake. A git-tracked flake is never deleted.

## Affected users and systems

TFactory backend verify lanes (nix_env.py). No API or config change.

## Constraints

- Never delete a git-tracked (repo-owned) flake.
- Keep the diff local to the lanes: another change is removing `repo_pvc`
  from the same file.
- Mypy ratchet must pass.

## Open questions

None.
