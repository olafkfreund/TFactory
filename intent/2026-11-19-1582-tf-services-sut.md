---
status: draft
issue: 1582
author: olafkfreund
---

# Intent: provision postgres/redis sidecars and start the SUT for api verification

## Problem

A spec for an app that needs postgres or redis (factory-lab-cloud PR #1,
aifactory-demo #624) cannot be verified. #1586 made TFactory stop early with
"unverifiable: needs services" when `.tfactory.yml` declares `services:`, but
nothing provisions them. Separately, the api lane only starts the app when
`detect_serve_command` finds it, and it misses the common `app/main.py` layout, so
`TFACTORY_TARGET_URL` is unset and every api test errors as "environment".

## Proposed outcome

When a spec's repo needs postgres and/or redis (declared in `.tfactory.yml`
`services:` or detected from docker-compose / requirements), the verify Job pod
runs them as sidecar containers reachable on loopback, exports `DATABASE_URL`
and `REDIS_URL`, waits until they accept connections, starts the SUT with
`uvicorn <module>:app` (or the declared `serve_command`), and sets
`TFACTORY_TARGET_URL`. If a sidecar or the SUT never comes up the result is an
infra verdict (existing `__TF_APP_NOT_HEALTHY__` path), not an AC failure. Where
the sandbox cannot provision (no nix runner configured), the #1586 early halt
stays.

## Affected users and systems

TFactory verify Jobs (`kube_sandbox.build_job_manifest`, `agents/nix_env.py`,
`agents/evaluator.py`, `agents/services_gate.py`). Spec authors with db/cache apps.

## Constraints

- No extra egress: sidecars share the pod network namespace; no NetworkPolicy change.
- Sidecars run non-root, caps dropped, no secrets: throwaway credentials on loopback only.
- Manifest change minimal and unit-tested for shape; default (no services) manifest is byte-identical.
- Needs a cluster that can pull the postgres/redis images (kubelet pull, overridable by env).
- Native sidecars need Kubernetes >= 1.29 (initContainer `restartPolicy: Always`) so the Job still completes.

## Open questions

None (owner delegation 2026-11-19: build the recommended design). A pre-generation
boot probe Job is not built; see spec.
