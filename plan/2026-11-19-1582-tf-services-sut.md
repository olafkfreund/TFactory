---
status: approved
issue: 1582
spec: spec/2026-11-19-1582-tf-services-sut.md
coders:
  1: codex   # contained manifest edit with exact security-context detail
  2: codex   # contained detection/gate logic with its own test file
  3: lead    # nix_env plumbing, touches the shared lane function: kept by the lead
reviewers:
  - codex    # read-only review of each draft and the final diff
---

# Plan: postgres/redis sidecars and SUT start in the verify Job (#1582)

Approved decisions (spec): services = (name, version) pairs from `.tfactory.yml`
`services:` plus detection (compose images, requirements/pyproject drivers). They run as
native sidecars (`initContainers` + `restartPolicy: Always`, appended after
`seed-nix-store`) in the verify Job pod on loopback; no NetworkPolicy change. Default
images `postgres:<v|16>-alpine` (uid 70, PGDATA=/pgdata/data on an emptyDir at /pgdata,
default socket dir, listen 127.0.0.1, POSTGRES_USER/PASSWORD/DB=app) and
`redis:<v|7>-alpine` (uid 999, `--bind 127.0.0.1 --save "" --appendonly no`); env overrides
`TFACTORY_SIDECAR_POSTGRES_IMAGE` / `TFACTORY_SIDECAR_REDIS_IMAGE`; version must match
`^[A-Za-z0-9._-]+$`. Sidecars: runAsNonRoot, drop ALL, no escalation, small limits.
Plumbing lives in `run_pytest_lane_via_nix` so every caller is covered.

## Steps

1. `apps/backend/tools/runners/kube_sandbox.py` `build_job_manifest` (~l.177-300): add
   `services: Sequence[tuple[str, str | None]] = ()`; for each, append a sidecar to
   `pod_spec["initContainers"]` (create the list when absent, keep seed-nix-store first) and
   an emptyDir volume for postgres. Add tests to `tests/test_kube_sandbox.py`: shape for
   postgres+redis (names, `restartPolicy: Always`, securityContext, env, args, volume,
   ordering after seed), version tag + invalid version fallback, env image override,
   `services=()` manifest equals an independent literal expected baseline (copy of the pre-change output) for the plain, `workspace_uri` and `nix_store_pvc` configurations.
   Verify: `cd apps/backend && python -m pytest ../../tests/test_kube_sandbox.py -q`.
   Traps: do not touch the `lane` container or `CONTAINER_SECURITY_CONTEXT`; ruff "as" alias sorting: plain imports.
2. `apps/backend/agents/services_gate.py`: add `required_services(spec_dir, project_dir)`
   returning `[(name, version)]` (declared, from the same loader/snapshot code, plus
   detected: compose files = `docker-compose*.y*ml`, `compose.y*ml` at the repo root, regex
   `image:\s*['\"]?(?:[\w./-]*/)?(postgres|redis)\b`; `requirements*.txt` and `pyproject.toml` at
   depth <= 2 (skip `.git`, `node_modules`, `.venv`, `venv`), case-insensitive names
   `psycopg`, `psycopg2`, `psycopg2-binary`, `asyncpg`, `pg8000` -> postgres and `redis`,
   `aioredis` -> redis. Result order postgres then redis, one entry per name, the declared
   entry (with its version) wins over a detected one); change `halt_if_services_declared` to halt only when declared services exist and
   `TFACTORY_NIX_RUNNER_IMAGE` is unset or `TFACTORY_VERIFY_BACKEND` in (host, docker).
   Update `tests/test_services_gate.py` and the affected `tests/test_gate_honesty.py` (~l.302):
   an autouse fixture clears `TFACTORY_NIX_RUNNER_IMAGE` and `TFACTORY_VERIFY_BACKEND`, capability
   tests set them and add tests: runner present -> no halt; backend=host and backend=docker -> halt; declared+detected dedupe and version precedence; detection from
   compose/requirements/pyproject; declared version kept; unknown driver names -> none.
   Verify: `python -m pytest ../../tests/test_services_gate.py -q`.
3. `apps/backend/agents/nix_env.py` `run_pytest_lane_via_nix` + `detect_serve_command`, and
   `agents/evaluator.py` (move `_API_SELF_SERVE_PORT = 8200` into nix_env; evaluator imports it from there):
   resolve services; `sandbox.with_manifest_kw(services=...)`; prelude exports
   DATABASE_URL/REDIS_URL and `/dev/tcp` waits (60s) emitting `__TF_APP_NOT_HEALTHY__`
   naming the sidecar. A failed sidecar wait OR a SUT that never accepts a connection sets
   `TF_NOT_HEALTHY=1`; the script then skips pytest and, per rerun (and mutants), emits
   `__PYTEST_RUN=<i>`, the marker line, `__PYTEST_EXIT=1` and exits 0, so `parse_pytest_exits`
   keeps the marker inside each run's tail and the batched path classifies infra
   (`app_not_healthy`); the single-run marker check triggers also when services requested; auto-detect
   `serve_command` for `lane == "api"` when none passed, none in `extra_env`, and no
   http `base_url` target in the snapshot; `app/main.py` detection. Tests in
   `tests/test_nix_env_services.py`: script prelude, marker, auto-serve on/off conditions, batched (reruns>1) output through `parse_pytest_exits` keeps the marker per run,
   detection of `app/main.py`.
   Verify: new test file plus existing nix_env tests (`pytest ../../tests -q -k "nix_env or serve"`).
   Traps: batched callers (`_nix_batched_signals`, `_nix_batched_stability`) pass no serve args; do not change them.
4. Run `scripts/ratchet_lint.py` for apps/backend and apps/web-server and the touched tests;
   commit with `--no-verify` if the unrelated mcp collection error blocks the hook (say so
   in the commit body). Rebase on origin/dev, push, PR to dev linking intent/spec/plan.

## Tests

`python -m pytest tests/test_kube_sandbox.py tests/test_services_gate.py tests/test_nix_env_services.py` plus the nix_env/evaluator suites; every new test must fail on dev without the change.

## Rollback

Revert the PR. With no services detected nothing changes (manifest byte-identical), so a
partial rollback is `services=()`.
