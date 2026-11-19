---
status: draft
issue: 1625
author: olafkfreund
---

# Intent: no Kubernetes service-link env in TFactory Jobs

## Problem

TFactory-created Job pods inherit Kubernetes service-link env vars (`REDIS_PORT=tcp://10.x.x.x:6379`, `*_SERVICE_HOST`) for every Service in the namespace. A system under test that reads `REDIS_PORT` as an int crashed at start (`int('tcp://...')`) on factory-lab-cloud PR #1, so the verify produced no verdict.

## Proposed outcome

No TFactory Job pod receives service-link env vars; the SUT sees only the env TFactory sets.

## Affected users and systems

Every Job built by `kube_sandbox.build_job_manifest` (verify lanes, and `verify_dispatch` which reuses it), `job_dispatch.build_job_manifest` (build Jobs), `portal_testing/dispatch.py`, and the chart Jobs `regression-cronjob.yaml` and `job-migrations.yaml`.

## Constraints

Nothing in these pods relies on service links (they reach services by DNS name or explicit env). Smallest change: one pod-spec field per builder.

## Open questions

None.
