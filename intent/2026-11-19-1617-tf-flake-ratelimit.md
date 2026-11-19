---
status: draft
issue: 1617
author: olafkfreund
---

# Intent: lane Jobs must not resolve unlocked flakes anonymously

## Problem

A repo that owns a `flake.nix` without `flake.lock` makes every lane Job resolve
`nixpkgs-unstable` through the unauthenticated GitHub API. Shared egress IPs hit
the rate limit (HTTP 403), `nix develop` dies before the test script runs, and
the shell lane reports `missing exit marker` (exit 1). The Triager then rejects
8/8 tests as a systematic failure although nothing was executed (factory-lab-shell
PR #1, task l-shell-pr1-lab-shellc-20261119, about 6.9 USD spent).

## Proposed outcome

- Lane Jobs can pass a GitHub token to nix (`access-tokens`) from a Kubernetes
  Secret, never in the manifest text or logs. Off until the owner configures it.
- When the Job script never started (no start marker, so `nix develop` failed
  before any test ran), the shell lane reports `not_run` (infra) with the real
  nix error text. A missing marker after the script started stays a failure
  (completed test evidence is preserved).

## Affected users and systems

Shell lane (`agents/nix_shell_lane.py`), Job manifest builder
(`tools/runners/kube_sandbox.py`), `nix_runner_from_env`, Helm chart env.

## Constraints

- No secret value is handled by this change; the Secret is created by the owner.
- Default behaviour with no Secret configured is unchanged except the verdict class.
- Related: #1581, #1582.

## Open questions

None. Decision (owner delegation): use the existing token-as-nix-access-token
option via secretKeyRef; the repo flake.lock is already honoured when present.
