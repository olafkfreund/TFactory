---
status: approved
issue: 1507
author: olafkfreund
---

# Intent: TFactory Jobs carry no ephemeral-storage limit

## Problem

The 4Gi `ephemeral-storage` limit exists only in the vendored hub `job_dispatch.build_job_manifest`, which TFactory never calls. Verify and lane Jobs are built by `kube_sandbox.build_job_manifest` (cpu and memory only), so a runaway nix build or Playwright cache fill grows on the node filesystem and kubelet evicts arbitrary pods at nodefs pressure.

## Proposed outcome

Every Job from `kube_sandbox.build_job_manifest` has a 4Gi `ephemeral-storage` limit (no request), so a runaway Job is evicted alone.

## Affected users and systems

TFactory verify orchestration Job and nix/playwright lane Jobs.

## Constraints

Limit only, request pinned to 0 (AIFactory#1425). Existing cpu/memory requests == limits unchanged. Overridable by parameter.

## Open questions

None.
