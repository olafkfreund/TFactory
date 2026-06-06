# performance-and-load-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: performance,load-testing,k6,locust,slo,thresholds,p95,p99,soak,spike,regression-gating

---

# Performance and Load Testing

Use this skill when you need to design or interpret load and performance tests — defining SLOs and thresholds (p95/p99 latency, error rate, throughput), choosing k6 or Locust, picking open vs closed workload models, running smoke / load / soak / spike / stress profiles, establishing baselines, and gating regressions in CI. Covers when performance testing belongs inside a TFactory run versus a dedicated perf pipeline.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Performance and Load Testing

Load testing is only useful if it has a *pass/fail line* — an SLO expressed as a threshold the test enforces. A run that prints "p95 = 412ms" tells you nothing; a run that fails because "p95 must be < 300ms" is a gate. This skill covers writing those gates in k6/Locust, picking the right workload model and profile, and deciding when a perf test belongs in a TFactory `integration`/`api` lane versus a separate perf pipeline.

---

## When to use this skill
- Writing a k6 or Locust script with explicit thresholds (p95/p99, error rate, RPS).
- Translating an SLO ("99% of requests under 300ms") into an enforceable threshold.
- Choosing between open and closed workload models for a realistic test.
- Selecting a profile: smoke, load, soak, spike, or stress.
- Setting a baseline and gating a regression in CI (fail the build on perf drift).
- Deciding whether performance testing belongs in this TFactory run or a dedicated perf environment.
- Do NOT trigger for: functional correctness tests (unit/api/browser lanes), accessibility (accessibility-testing skill), or cloud posture (cloud-posture-testing). Also don't run heavy load against shared/prod infra without sign-off.

---

## Key principles
1. **No threshold, no test** — a perf run without an asserted threshold is a benchmark, not a test. Every script must define pass/fail (k6 `thresholds`, Locust assertions on stats).
2. **Percentiles over averages** — averages hide tail latency. Gate on p95/p99, not mean; one slow tail can violate an SLO an average happily passes.
3. **Open model for arrivals, closed model for users** — model real traffic as an open arrival rate when you care about "requests per second the system faces"; use a closed model when you're simulating a fixed pool of concurrent users.
4. **Baseline before you gate** — you can't detect regression without a known-good baseline. Capture one on stable infra, then gate deltas against it.
5. **Right profile for the question** — smoke proves the script works, load proves the SLO at expected traffic, soak finds leaks over time, spike finds elasticity, stress finds the breaking point. Don't conflate them.
6. **Isolate the environment** — perf numbers are meaningless on noisy/shared infra. Pin a dedicated environment and warm it before measuring.
7. **Gate on relative drift, not just absolute** — absolute thresholds (p95 < 300ms) catch SLO breaches; relative gates (p95 within +10% of baseline) catch creeping regressions before they breach.
8. **Don't load-test in TFactory's sandbox** — the `--network=none` functional sandbox can't reach a target and isn't sized for throughput. Keep real load in a dedicated pipeline; only threshold smokes belong near a lane.
9. **Think time models reality** — real users pause between actions; back-to-back requests with zero think time produce an unrealistic, pessimistic load. Model `wait_time`/sleep to match actual usage unless you're deliberately stress-testing.

---

## Core concepts
**SLO → threshold** — the SLO is the business promise ("99% under 300ms, error rate < 0.1%"); the threshold is its machine-enforceable form in the test runner. The test fails when the threshold is missed.

**Open vs closed model** — *closed*: a fixed number of virtual users, each fires the next request only after the previous returns (throughput self-limits under load — masks degradation). *open*: requests arrive at a target rate regardless of response time (queue builds under load — exposes degradation). Open models are more honest for capacity questions.

**Profiles** —
- *smoke*: 1–few users, short — verifies the script and target are alive.
- *load*: expected production traffic for a sustained window — validates the SLO.
- *soak*: moderate load for hours — surfaces memory leaks, connection-pool exhaustion, slow degradation.
- *spike*: sudden jump to high load — tests autoscaling/elasticity and recovery.
- *stress*: ramp past capacity — finds the breaking point and failure mode.

**Baseline** — a recorded known-good run on stable infra; the reference for regression gating.

**Regression gate** — CI step that compares the current run's percentiles/error-rate against the baseline (absolute thresholds and/or relative deltas) and fails the build on violation.

**k6 vs Locust** — k6: JS scripts, native `thresholds`, strong for open-model arrival-rate executors and CI gating. Locust: Python scripts, class-based user behavior, good for complex stateful user journeys and a live web UI.

**Where perf fits TFactory's lanes** — TFactory's v0.2 spine is unit / browser / api / integration / mutation, executed in a sandboxed `--network=none --read-only` container. That sandbox is built for *functional* verification, not sustained throughput, and it forbids the very network a load test needs. So a heavy load test does not belong inside a TFactory run; a *threshold smoke* (a handful of requests asserting p95 < target against a reachable test environment) can live at the edge of an `api`/`integration` lane, but real load is a dedicated pipeline concern.

**Warm-up window** — the period at the start of a run where caches, JITs, and connection pools are cold and latency is artificially high. Measured percentiles must exclude (or ramp through) this window or the tail is polluted by startup, not steady-state behavior.

**Throughput vs concurrency** — concurrency is how many requests are in flight; throughput (RPS) is how many complete per second. Under a closed model they're coupled (more users only helps if responses are fast); under an open model you set throughput directly and watch concurrency balloon as the system slows — which is exactly the signal you want.

**SLO budget framing** — an SLO like "99% under 300ms" implies a 1% error budget for the tail. A regression gate that fails on p99 ≥ 300ms is enforcing that budget; pairing it with a relative gate (p95 within +10% of baseline) catches the slow creep that erodes the budget before it breaches.

---

## Common tasks
### Write a k6 script with thresholds
```javascript
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    load: { executor: 'constant-arrival-rate', rate: 200, timeUnit: '1s',
            duration: '5m', preAllocatedVUs: 50, maxVUs: 200 }, // open model
  },
  thresholds: {
    http_req_duration: ['p(95)<300', 'p(99)<500'],  // SLO as gate
    http_req_failed: ['rate<0.001'],                 // error rate < 0.1%
  },
};

export default function () {
  const res = http.get('https://target/api/health');
  check(res, { 'status 200': (r) => r.status === 200 });
}
```
k6 exits non-zero when a threshold fails — that's your CI gate.

### Write a Locust user with a stat gate
```python
from locust import HttpUser, task, between

class ApiUser(HttpUser):
    wait_time = between(1, 2)  # closed model: fixed pool of users

    @task
    def health(self):
        self.client.get("/api/health", name="health")

# Gate in CI: locust --headless -u 50 -r 10 -t 5m --csv=run
# then fail if 95%ile or failure ratio exceeds SLO (parse run_stats.csv).
```

### Pick smoke vs load vs soak vs spike
- New script / target sanity → smoke (`-u 1 -t 30s`).
- Validate the SLO → load (expected RPS, 5–15m).
- Hunt leaks → soak (moderate load, 1–4h).
- Test autoscaling → spike (jump to 5–10× for a short burst, watch recovery).

### Gate a regression in CI
Store the baseline run's percentiles, then fail the job if the new run breaches absolute thresholds OR drifts > N% from baseline. Treat a failed threshold as a build failure, not a warning.

### Decide in-TFactory vs dedicated pipeline
Put a *lightweight* perf smoke/threshold check in TFactory's `api`/`integration` lane when it's fast, hermetic, and deterministic. Move *real* load (soak, spike, high-RPS, shared infra) to a dedicated perf pipeline with isolated infrastructure — TFactory's sandbox is built for functional verification, not sustained high-throughput load.

### Translate an SLO into a k6 threshold set
Write the SLO down in plain language, then encode each clause as a threshold:
- "99% of requests under 300ms" → `http_req_duration: ['p(99)<300']`
- "error rate below 0.1%" → `http_req_failed: ['rate<0.001']`
- "sustain 200 RPS" → an `constant-arrival-rate` scenario at `rate: 200` that doesn't breach the latency gate.
A clause with no threshold is an SLO you're not actually testing.

### Stage a profile ramp in k6
Use `ramping-arrival-rate` to grow load and watch where the SLO breaks — this is how you find capacity, not just confirm it:
```javascript
export const options = {
  scenarios: {
    capacity: {
      executor: 'ramping-arrival-rate', startRate: 50, timeUnit: '1s',
      preAllocatedVUs: 50, maxVUs: 500,
      stages: [
        { target: 100, duration: '2m' },
        { target: 300, duration: '5m' },  // open model exposes degradation here
        { target: 0,   duration: '1m' },
      ],
    },
  },
  thresholds: { http_req_duration: ['p(95)<300'] },
};
```

### Store and diff a baseline in CI
Persist each run's summary (p95/p99/error-rate) as a CI artifact. On the next run, fail the job if the new numbers breach absolute thresholds OR drift beyond a relative tolerance from the stored baseline. Update the baseline only on an intentional, reviewed change — never silently on every green run, or you'll baseline-in a regression.

### Pick the model that answers your question
Match the workload model to what you're actually measuring:
- "How many requests/sec can we sustain under SLO?" → **open** arrival-rate model; you set RPS and watch latency/concurrency.
- "How does the system behave with N concurrent users?" → **closed** model with N VUs and think time.
- "Where does it break?" → ramping open model (`ramping-arrival-rate`) climbing past expected capacity.
Using a closed model for the first question is the most common mistake — it self-limits and hides the very degradation you're hunting.

### Define an SLO/threshold table before scripting
Write the contract first, then implement it; a threshold without a stated SLO is unaccountable.
| SLO clause | Threshold | Profile |
|---|---|---|
| p95 latency < 300ms | `p(95)<300` | load |
| p99 latency < 500ms | `p(99)<500` | load |
| error rate < 0.1% | `rate<0.001` | load + soak |
| no memory growth over 2h | external memory gate | soak |
| recovers within 60s after spike | post-spike p95 gate | spike |

### Keep TFactory's role honest
In a TFactory `api`/`integration` lane, a perf check should be a *deterministic threshold smoke* — a few requests against a reachable test environment asserting a latency bound — not a load test. Anything that needs sustained RPS, hours of soak, or shared infrastructure is a dedicated perf pipeline's job, because TFactory's sandbox is network-isolated and sized for functional verification.

---

## Gotchas
1. **Closed model masks degradation** — with fixed VUs, slow responses just lower throughput, so latency looks fine while the system is actually overloaded. Use an open arrival-rate model to expose it.
2. **Averages lie about tails** — a 50ms average can hide a 2s p99. Always gate on percentiles, never mean.
3. **No warm-up skews results** — cold caches/JITs/connection pools inflate early latency. Discard or ramp a warm-up window before measuring.
4. **Running load from one underpowered box** — the load generator becomes the bottleneck and you measure *its* limits, not the target's. Watch generator CPU/network; distribute if saturated.
5. **Baseline on noisy infra is worthless** — a baseline captured on shared/contended hardware makes every later comparison meaningless. Pin dedicated, quiet infra.
6. **Soak too short to find leaks** — memory leaks and pool exhaustion need hours, not minutes. A 5-minute "soak" is just a load test.
7. **Gating only on absolute thresholds** — a system that quietly drifts from 100ms to 290ms passes a `<300ms` gate while regressing badly. Add a relative-to-baseline gate.
8. **Zero think time inflates the bottleneck** — hammering with no pause measures a workload no real user generates and finds false hotspots. Add realistic think time unless intentionally stressing.
9. **Re-baselining on every green run** — auto-updating the baseline each pass lets a slow regression become the new normal. Update the baseline only on reviewed, intentional changes.
10. **Comparing runs from different environments** — a number from staging means nothing against a baseline from prod-like infra. Pin one environment for both baseline and comparison.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Perf run with no threshold | It's a benchmark, not a pass/fail test | Define k6 `thresholds` / Locust stat gates from the SLO |
| Gating on average latency | Averages hide tail latency that breaks SLOs | Gate on p95/p99 |
| Always using a closed (fixed-VU) model | Self-limiting throughput masks overload | Use open arrival-rate model for capacity questions |
| No baseline before regression gating | Can't detect drift without a reference | Capture a known-good baseline on isolated infra first |
| Calling a 5-min run a "soak" | Leaks need hours to surface | Run soaks for hours under sustained load |
| Heavy load against shared/prod infra | Skews results and risks an outage | Use a dedicated, isolated perf environment with sign-off |
| Putting full load tests in TFactory's sandbox | The sandbox is for functional verification | Keep only light perf smoke in-lane; move real load to a perf pipeline |
| Ignoring load-generator saturation | You measure the generator, not the target | Monitor generator resources; distribute when saturated |
