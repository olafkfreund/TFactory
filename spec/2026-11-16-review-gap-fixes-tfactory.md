---
status: draft
issue: 1507
intent: intent/2026-11-16-review-gap-fixes-tfactory.md
---

# Spec: ephemeral-storage limit in kube_sandbox

## Design

Add `ephemeral_storage_limit: str = "4Gi"` to `kube_sandbox.build_job_manifest` and put it in container `resources.limits` only, mirroring the hub default (job_dispatch.py:179, :591). All callers use the builder's defaults, so no caller changes.

## Alternatives rejected

- Call the hub builder: it has a different shape (no workspace_uri, nix store PVC, service account); a rewrite is far larger.
- Add a request too: nodes over-advertise allocatable storage (AIFactory#1425), so a request would admit builds against a pool that does not exist.

## Risks

emptyDir `/work` counts toward the limit; a lane unpacking a workspace over 4Gi is evicted. Mitigated by the parameter; the hub's measured worst case is 2.4 GB.

## Verification

New test asserts the limit and no request on a built manifest (fails before the fix). Existing resource tests updated to the new limits dict.
