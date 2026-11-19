---
status: draft
issue: 1582
intent: intent/2026-11-19-1582-tf-services-sut.md
---

# Spec: postgres/redis sidecars and SUT start in the verify Job

## Design

1. **Service set** (`agents/services_gate.py`, new `required_services(spec_dir, project_dir) -> list[tuple[str, str | None]]`):
   (name, version) pairs. Declared `.tfactory.yml` `services:` (existing loader/snapshot
   logic, factored out of `halt_if_services_declared`; keeps `ServiceSpec.version`) union
   detected (version None): `docker-compose*.yml`/`compose.y*ml` at repo root with
   `image: postgres|redis`; `requirements*.txt` / `pyproject.toml` (depth <= 2) naming
   psycopg/psycopg2/asyncpg -> postgres, redis/aioredis -> redis. Order fixed
   (postgres, redis); declared wins for version.
2. **Gate**: `halt_if_services_declared` keeps its name and #1586 reason text but halts only
   when services are declared AND provisioning cannot happen: `TFACTORY_NIX_RUNNER_IMAGE`
   unset, or `TFACTORY_VERIFY_BACKEND` is `host`/`docker` (those paths bypass sidecars).
   Detected-only services never halt (repo may fall back to sqlite). A missing nix env makes
   the lane return None and fall back; that late case is accepted (see Risks).
3. **Manifest** (`kube_sandbox.build_job_manifest(..., services=())`, items `(name, version)`):
   each service becomes a native sidecar, an `initContainers` entry with
   `restartPolicy: Always` (appended after seed-nix-store), so the Job still completes when
   `lane` exits and `_run_job` still reads `container="lane"`. Default images
   `postgres:<v|16>-alpine` / `redis:<v|7>-alpine` (version must match `^[A-Za-z0-9._-]+$`,
   else default), overridable whole by `TFACTORY_SIDECAR_POSTGRES_IMAGE` /
   `TFACTORY_SIDECAR_REDIS_IMAGE`. Security: `runAsNonRoot`, fixed `runAsUser` matching the
   default images (postgres alpine 70, redis alpine 999), drop ALL caps, no escalation, small
   cpu/memory limits. Postgres: `PGDATA=/pgdata/data` with an emptyDir mounted at `/pgdata`
   (emptyDir is world-writable, the child dir is created by the postgres uid), default socket
   dir kept (the entrypoint's init `psql` needs it; the image makes /var/run/postgresql
   world-writable), `-c listen_addresses=127.0.0.1`, `POSTGRES_USER/PASSWORD/DB=app`
   (throwaway, loopback). Redis: `--bind 127.0.0.1 --save "" --appendonly no`.
   With `services=()` the manifest is unchanged.
4. **Plumbing in the common lane function** (`agents/nix_env.run_pytest_lane_via_nix`), so
   every caller (evaluator single run, `_nix_batched_stability`, `_nix_batched_signals`,
   regression runner) is covered: it resolves `required_services` itself, applies
   `sandbox.with_manifest_kw(services=...)`, and writes a Job-script prelude before the SUT
   start: export `DATABASE_URL=postgresql://app:app@127.0.0.1:5432/app` and
   `REDIS_URL=redis://127.0.0.1:6379/0`, then wait (bash `/dev/tcp`, 60s) for each port; on
   timeout echo the existing `__TF_APP_NOT_HEALTHY__` marker naming the sidecar, so the
   verdict is infra. The marker check currently requires `serve_command`; it also triggers
   when services were requested.
5. **SUT start**: the batched callers never pass `serve_command`, so api-lane subtasks on
   the nix path never self-served. `run_pytest_lane_via_nix` now detects it itself when
   `lane == "api"`, no `serve_command` was passed, no `TFACTORY_TARGET_URL` in `extra_env`,
   and the snapshotted `.tfactory.yml` declares no http target `base_url` (external target
   wins). `detect_serve_command` also finds `app/main.py` -> `uvicorn app.main:app`
   (factory-lab-cloud layout); a declared `serve_command` still wins. The existing in-Job
   boot, 30s connect poll and `TFACTORY_TARGET_URL` export are reused. Port 8200 (the
   evaluator's `_API_SELF_SERVE_PORT`, moved to nix_env and re-imported).

## Alternatives rejected

- **Separate service pods/Deployments + NetworkPolicy**: new egress, lifecycle and cleanup; sidecars reach loopback with no policy change.
- **Pre-generation boot probe Job** (fail closed before generation spend): adds a second cold nix Job per spec for a case the in-Job marker already classifies as infra. The pre-spend guarantee is kept for the cheap case (declared services, no runner -> #1586 halt). Not built; noted for a follow-up.
- **Run postgres/redis inside the runner image**: bloats the image shared by every lane and runs a daemon as the root nix user.
- **Classic sidecar containers**: the Job never completes because the sidecar never exits.

## Risks

- Declared services with a runner but no nix env: the lane returns None, falls back to host, tests error as environment (late, after generation). Accepted; same as today for any nix-less spec.
- Cluster < 1.29 or image pull blocked: pod does not start; Job ends with a pod diagnostic and an infra-style not_run. Mitigation: image env overrides.
- Port clash if a repo's tests start their own postgres: unlikely, and loopback is pod-scoped.
- Detection false positive starts an unused sidecar: cost is a few seconds and 128Mi.
- Sidecar resources add to pod requests (small, bounded).

## Verification

- Codex review (round 1) confirmed: native sidecars fit Job completion/log reading; fixed postgres socket/ownership, batched callers, backend predicate, version propagation.
- Unit: manifest shape with/without services (names, restartPolicy, securityContext, ordering after seed init container, default byte-identical); `required_services` for declared/compose/requirements; gate halts only without runner; Job script prelude contains exports and wait; marker when services requested; `detect_serve_command` for `app/main.py`.
- Each test fails without the change.
- `scripts/ratchet_lint.py` for apps/backend and apps/web-server, full touched test files.
- Live validation (postgres image pull and native sidecar behaviour on the k3d cluster) is for the 0.12.3 release soak: not done here (no live cluster changes).
