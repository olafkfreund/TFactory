---
status: draft
issue: 1582
intent: intent/2026-11-19-1582-tf-services-sut.md
---

# Spec: postgres/redis sidecars and SUT start in the verify Job

## Design

1. **Service set** (`agents/services_gate.py`, new `required_services(spec_dir, project_dir) -> list[str]`):
   declared `.tfactory.yml` `services:` (existing loader/snapshot logic, factored
   out of `halt_if_services_declared`) union detected: `docker-compose*.yml`/`compose.y*ml`
   at repo root with `image: postgres|redis`; `requirements*.txt` / `pyproject.toml`
   (depth <= 2) naming psycopg/psycopg2/asyncpg -> postgres, redis/aioredis -> redis.
   Order fixed (postgres, redis), names restricted to those two.
2. **Gate**: `halt_if_services_declared` keeps its name and #1586 reason text but
   halts only when services are declared AND the nix sandbox is not configured
   (`TFACTORY_NIX_RUNNER_IMAGE` unset, i.e. nothing can provision). With a runner
   the pipeline proceeds. Detected-only services never halt (repo may fall back to sqlite).
3. **Manifest** (`kube_sandbox.build_job_manifest(..., services=())`): each service
   becomes a native sidecar, an `initContainers` entry with `restartPolicy: Always`
   (appended after the seed-nix-store init container), so the Job still completes when
   `lane` exits. Images default to `postgres:16-alpine` / `redis:7-alpine`, overridable
   by `TFACTORY_SIDECAR_POSTGRES_IMAGE` / `TFACTORY_SIDECAR_REDIS_IMAGE`; version from
   `ServiceSpec.version` is applied to the default tag. Each runs `runAsUser: 999`,
   `runAsNonRoot`, drop ALL caps, no escalation, small cpu/memory limits, bound to
   127.0.0.1, data on an emptyDir (postgres: `POSTGRES_USER/PASSWORD/DB=app`, throwaway,
   loopback only, `unix_socket_directories=/tmp`; redis: no persistence). With
   `services=()` the manifest is unchanged.
4. **Plumbing**: `evaluator` computes `required_services` and, when non-empty and a
   nix runner exists, `run_pytest_lane_via_nix(..., services=...)` uses
   `sandbox.with_manifest_kw(services=...)`. The Job script gains a prelude, before the
   SUT start: export `DATABASE_URL=postgresql://app:app@127.0.0.1:5432/app` (also
   `POSTGRES_*`-free) and `REDIS_URL=redis://127.0.0.1:6379/0`, then wait (bash
   `/dev/tcp`, 60s) for each port; on timeout echo the existing
   `__TF_APP_NOT_HEALTHY__` marker with the sidecar named, so the verdict is infra.
   The marker handling currently requires `serve_command`; extend it to also trigger
   when services were requested.
5. **SUT start**: `detect_serve_command` additionally finds `app/main.py` ->
   `uvicorn app.main:app` (factory-lab-cloud layout). A declared `serve_command` still
   wins. The existing in-Job boot, 30s connect poll and `TFACTORY_TARGET_URL` export are reused.

## Alternatives rejected

- **Separate service pods/Deployments + NetworkPolicy**: new egress, lifecycle and cleanup; sidecars reach loopback with no policy change.
- **Pre-generation boot probe Job** (fail closed before generation spend): adds a second cold nix Job per spec for a case the in-Job marker already classifies as infra. The pre-spend guarantee is kept for the cheap case (declared services, no runner -> #1586 halt). Not built; noted for a follow-up.
- **Run postgres/redis inside the runner image**: bloats the image shared by every lane and runs a daemon as the root nix user.
- **Classic sidecar containers**: the Job never completes because the sidecar never exits.

## Risks

- Cluster < 1.29 or image pull blocked: pod does not start; Job ends with a pod diagnostic and an infra-style not_run. Mitigation: image env overrides.
- Port clash if a repo's tests start their own postgres: unlikely, and loopback is pod-scoped.
- Detection false positive starts an unused sidecar: cost is a few seconds and 128Mi.
- Sidecar resources add to pod requests (small, bounded).

## Verification

- Unit: manifest shape with/without services (names, restartPolicy, securityContext, ordering after seed init container, default byte-identical); `required_services` for declared/compose/requirements; gate halts only without runner; Job script prelude contains exports and wait; marker when services requested; `detect_serve_command` for `app/main.py`.
- Each test fails without the change.
- `scripts/ratchet_lint.py` for apps/backend and apps/web-server, full touched test files.
- Live validation (postgres image pull and native sidecar behaviour on the k3d cluster) is for the 0.12.3 release soak: not done here (no live cluster changes).
