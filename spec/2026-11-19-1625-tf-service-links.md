---
status: draft
issue: 1625
intent: intent/2026-11-19-1625-tf-service-links.md
---

# Spec: enableServiceLinks false on every Job pod

## Design

Add `"enableServiceLinks": False` to the pod spec in the three builders that create TFactory Jobs: `apps/backend/tools/runners/kube_sandbox.py`, `apps/backend/tools/runners/job_dispatch.py`, `portal_testing/dispatch.py`, plus `enableServiceLinks: false` in the chart templates `regression-cronjob.yaml` and `job-migrations.yaml` (Codex review finding). `verify_dispatch` reuses the kube_sandbox builder, so it is covered.

## Alternatives rejected

Unsetting the vars in the Job script: misses build Jobs and leaves the pod env polluted. Namespace-level change: not ours to own, and other pods may rely on links.

## Risks

A pod that read `<SVC>_SERVICE_HOST` would break; grep shows none in TFactory Job scripts. DNS service discovery is unaffected.

## Verification

A test per builder asserts `enableServiceLinks is False`; each fails without the field.
