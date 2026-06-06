# test-environment-orchestration

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: testing, environments, docker-compose, kubernetes, health-check, tfactory-yml, test-targets, orchestration

---

# Test Environment Orchestration

Use this skill when declaring test targets in `.tfactory.yml` (http, docker_compose, kubernetes, docker_run, cloud_provider), gating lanes behind a health check before tests run, resolving the target URL the tests hit, standing up and tearing down an application-under-test, or debugging why a TFactory lane never started because the environment was not ready.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Test Environment Orchestration

TFactory's browser and api lanes need a *live* application to test, not just source code. This skill covers how the application-under-test is declared, brought up, health-gated, and addressed inside the hardened Docker sandbox. Get this wrong and every browser/api test fails with a connection refused that looks like a test bug but is really an environment bug.

The contract is simple: declare a target in `.tfactory.yml`, TFactory probes it healthy *before* it dispatches lanes, injects `TFACTORY_TARGET_URL` into the test container, and tears the target down afterward.

---

## When to use this skill
- Adding or editing a `target:` block in `.tfactory.yml` (http / docker_compose / kubernetes / docker_run / cloud_provider).
- A browser or api lane fails immediately with `ECONNREFUSED` / `Connection refused` and you suspect the app never came up.
- You need the health gate to wait longer (slow app boot) or probe a different path.
- Standing up a compose stack or k8s port-forward purely for the duration of a test run.
- Wiring a build→deploy→test flow (`docker_run`, #233) where the image is built fresh per run.
- Figuring out *which* URL the tests will actually hit (override vs base_url vs port-forward vs ingress).

Do NOT trigger for:
- Login/credentials for the app (that is `test-target-authentication`).
- Sandbox network isolation / read-only mount rules (that is `sandbox-and-test-security`).
- Cloud *posture* assessment (CSPM) — that is the cloud-discovery flow, not a test target.

---

## Key principles
1. **Declare, don't script** — The target lives declaratively in `.tfactory.yml`. TFactory owns the lifecycle (up → health gate → inject URL → lanes → teardown). Do not hand-roll `docker compose up` in a test.
2. **Health-gate before lanes** — No lane dispatches until `health_check` passes. A failed gate fails the *run* with an environment error, not a flood of misleading test failures.
3. **One canonical URL** — Tests read the target address from one place: the injected `TFACTORY_TARGET_URL`. Never hard-code `http://localhost:3000` in a generated test.
4. **Unit lane needs no target** — Unit tests run `--network=none`; they never touch a target. Only `browser` / `api` / `integration` lanes consume the environment.
5. **Resolution precedence is fixed** — `TFACTORY_TARGET_URL` override > `base_url` > k8s `port_forward` > ingress. Know the order when debugging "why did it hit the wrong host".
6. **Ephemeral by default** — A target stood up for a run is torn down after it. Persistent infra (a shared staging URL) uses `http` with no lifecycle, just a probe.
7. **Health check is a gate, not a smoke test** — Keep it cheap and deterministic (a `/healthz` 200). Heavy readiness logic belongs in the app, not the probe.

---

## Core concepts
**target** — The `.tfactory.yml` block describing where/how the app-under-test runs. One of `http`, `docker_compose`, `kubernetes`, `docker_run`, `cloud_provider`.

**health_check** — A probe (path + expected status + timeout/retries) that must pass before lanes dispatch. The boot gate for the whole run.

**TFACTORY_TARGET_URL** — The resolved base URL injected into every networked lane's container. The single source of truth for tests.

**AppRuntime** — The compose-managed runtime: TFactory brings the stack up, waits for the `health` of the named service, runs lanes against it, then `down`s it.

**port-forward dispatch (#108)** — For `kubernetes` targets, TFactory runs `kubectl port-forward` to expose an in-cluster service on a sandbox-local port, then points `TFACTORY_TARGET_URL` at it.

**build→deploy→test (#233, `docker_run`)** — Build the image from the repo, run it as a container, health-gate, test, tear down — all within the run.

**egress gate** — `egress.enabled` controls whether the networked lanes may reach beyond the target (default deny). Covered in security skills; relevant here because a target on the public internet needs egress allowed.

**cloud_provider target** — A target type for cloud-hosted endpoints; pairs with the credential broker for cloud-issued credentials. Distinct from cloud *posture* assessment (CSPM), which is a separate read-only flow, not a test target.

**wait_for vs health_check** — Two gates that compose: `wait_for` waits on the *orchestrator's* notion of readiness (compose service `healthy`, k8s endpoints ready); `health_check` then probes the *app's* HTTP health path. Both must pass before lanes dispatch.

---

## Common tasks

### Point tests at an existing HTTP service (shared staging)
No lifecycle — TFactory just probes and runs.
```yaml
# .tfactory.yml
target:
  type: http
  base_url: https://staging.example.com
  health_check:
    path: /healthz
    status: 200
    timeout: 30s
    retries: 10
egress:
  enabled: true   # target is off-box, lanes need egress
```

### Stand up a docker_compose stack for the run
TFactory `up`s the stack, waits for the named service's health, runs lanes, then `down`s it.
```yaml
target:
  type: docker_compose
  file: docker-compose.test.yml
  service: web            # the AppRuntime service tests hit
  base_url: http://web:8080
  wait_for:
    service: web
    health: healthy        # honors the compose healthcheck
    timeout: 120s
  health_check:
    path: /healthz
    status: 200
```

### Test an in-cluster service via kubernetes port-forward (#108)
```yaml
target:
  type: kubernetes
  context: ci-cluster
  namespace: app
  service: web-svc
  port: 8080               # in-cluster port
  port_forward: true       # kubectl port-forward -> sandbox-local port
  health_check:
    path: /healthz
    status: 200
    timeout: 60s
```
TFactory resolves `TFACTORY_TARGET_URL=http://127.0.0.1:<local>` from the forward.

### Build→deploy→test in one run (#233)
```yaml
target:
  type: docker_run
  build:
    context: .
    dockerfile: Dockerfile
  run:
    ports: ["8080:8080"]
    env:
      APP_ENV: test
  base_url: http://127.0.0.1:8080
  health_check:
    path: /healthz
    status: 200
    timeout: 90s
    retries: 15
```

### Override the URL at dispatch time
When you must point an existing run at a one-off host (debugging), set the env override — it beats `base_url`:
```bash
TFACTORY_TARGET_URL=http://127.0.0.1:9999 python run.py --spec 001
```

### Make the health gate wait longer for a slow boot
Bump `timeout` and `retries` together; the gate retries `path` until it sees `status` or the budget expires.
```yaml
health_check:
  path: /ready
  status: 200
  timeout: 180s
  retries: 30      # ~6s between attempts across the budget
```

### Understand URL resolution precedence (debugging "wrong host")
When a lane hits an unexpected host, walk the precedence top-down — the first that's set wins:
```
1. TFACTORY_TARGET_URL   (env override — set at dispatch)   ← highest
2. target.base_url       (declared in .tfactory.yml)
3. kubernetes port_forward (resolved 127.0.0.1:<local>)
4. ingress               (cluster ingress host)             ← lowest
```
So an env override silently beats a `base_url`; unset it to fall back.

### Confirm what the lane actually resolved
The resolved URL is injected as `TFACTORY_TARGET_URL`; echo it from inside a lane to confirm.
```bash
# inside a generated test's setup, for debugging only:
echo "target = $TFACTORY_TARGET_URL"   # what the lane will hit
```

### Teardown is automatic — but clean up after a crash
Normal completion `down`s the stack. After a SIGKILL, sweep manually:
```bash
docker compose -f docker-compose.test.yml down -v   # compose target
pkill -f 'kubectl port-forward'                      # k8s target
```

---

## Gotchas
1. **`base_url` host must be reachable from inside the sandbox** — A compose service is addressed by its *service name* on the compose network (`http://web:8080`), not `localhost`. `localhost` inside the test container is the container, not your host.
2. **Health gate passes but tests still 404** — The gate only checks `health_check.path`. If your app serves health on `/healthz` but the app routes on `/`, the gate is green while the real routes 404. Probe a path that proves the app is actually serving.
3. **k8s port-forward dies mid-run** — A dropped `kubectl port-forward` kills connectivity for the rest of the lanes. Ensure the kubeconfig/context is valid and the service has ready endpoints *before* the run, or the forward flaps.
4. **Unit lane "can't reach the target"** — It shouldn't try. Unit runs `--network=none`. If a "unit" test needs the target, it is mis-laned — move it to `api`/`integration`.
5. **Egress disabled + off-box target** — An `http` target on the public internet with `egress.enabled: false` will never be reachable; the health gate fails. Enable egress for off-box targets.
6. **Teardown skipped on crash leaves the stack up** — If the run is killed (SIGKILL) mid-flight, compose/`docker_run` containers may linger. Add a manual `docker compose -f docker-compose.test.yml down` to your debug cleanup.
7. **Two targets, one run** — A `.tfactory.yml` declares one `target`. You cannot health-gate two independent services in one run; compose them into one stack (`docker_compose`) instead.

8. **Override left set across runs** — A `TFACTORY_TARGET_URL` exported in a shell session silently overrides `base_url` for *every* subsequent run, pointing tests at a stale host. Unset it when you're done debugging.

9. **`wait_for` healthy but app not migrated** — A compose service can report `healthy` (process up) before DB migrations finish. The `health_check` path should depend on a migrated/ready state, not just the process being alive.

10. **docker_run port not published** — A `docker_run` target whose `run.ports` doesn't map the app port leaves `base_url` pointing at a closed port; the health gate times out. Publish the port the app listens on.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Hard-coding `http://localhost:3000` in a generated test | Breaks the moment the target moves (compose net, k8s, override) | Read `TFACTORY_TARGET_URL` from the env |
| Running `docker compose up` inside a test | Bypasses the health gate and teardown; races the lanes | Declare a `docker_compose` target; let TFactory own the lifecycle |
| Skipping `health_check` to "save time" | Lanes dispatch against a not-yet-listening app → false test failures | Always declare a cheap deterministic health gate |
| Probing `/` as the health path on an SPA | `/` may 200 from the static shell before the API is ready | Probe a real `/healthz`/`/ready` that depends on app readiness |
| Putting a heavy readiness query in the health probe | Slow/flaky gate becomes the bottleneck and a flake source | Keep the probe trivial; put readiness logic in the app's `/ready` |
| Using `localhost` for a compose service | `localhost` in-container ≠ the service container | Address by compose service name on the compose network |
| Leaving `egress.enabled: false` for a public target | Health gate can't reach it; whole run fails | Enable egress only for the off-box hosts you actually need |
| Assuming `base_url` wins over the env override | Resolution is override > base_url > port-forward > ingress | Set `TFACTORY_TARGET_URL` only when you intend to override |
