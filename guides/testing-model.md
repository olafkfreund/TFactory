# TFactory testing model

> The single source of truth for *what* TFactory can test, *how* it reaches
> targets, *how* tests are scored, and *how* results are verified in the repo +
> Backstage. Consolidated under epic #232.

## 1. What we can test

| Capability | Status |
|---|---|
| Python — pytest (unit + api), Cobertura coverage | ✅ |
| TypeScript/JS — Jest (unit, LCOV) | ✅ |
| TypeScript — Playwright (browser, via AppRuntime) | ✅ |
| Mutation — Python (`mutate_probe`) + TypeScript (Stryker) | ✅ |
| Cloud CSPM — AWS / GCP / Azure read-only posture (epic #133) | ✅ |
| Cypress (browser) / Vitest (unit) — TypeScript | ✅ descriptors + runner images + CI build (#110/#236) |
| Java — JUnit 5 + JaCoCo + PIT mutation | 🟡 wedge: descriptor + runner image + probe + dispatch (#237); live PIT/JaCoCo via CI |
| C# / Go / Rust / Ruby | ❌ planned |
| App SAST / DAST | ❌ out of scope (DEC-002) — delegated to security pipelines |

Lanes: **unit · browser · api · integration · mutation** (browser-first by
Decision 3). A single plan can mix languages/frameworks (descriptor registry).

## 2. How code-under-test enters

The snapshotter freezes the AIFactory handover into the workspace:
`context/source.json` (branch + base_ref + repo), `context/diff.patch`
(`git diff base_ref..branch`), `context/aifactory_spec.md`. The Planner reads
these; tests are generated into `<spec>/tests/`, never into the SUT.

## 3. Pre-deploy vs post-deploy vs build→deploy→test

**Do we build before testing?** For Python and TypeScript, **no** — pytest runs
source directly; Jest/ts-jest transpiles at test time. Compiled languages
(Java/Go/Rust) are not yet supported.

- **Pre-deploy (just built, not deployed):** the Executor copies the SUT into a
  scratch dir, mounts it read-only in a Docker sandbox (`--network=none` for
  unit), copies the generated test in, and runs the framework. No deployment.
- **Post-deploy (something running):**
  - **docker-compose** — `AppRuntime` spins up services, health-polls, injects
    `TFACTORY_TARGET_URL`, runs the browser/integration lane, tears down.
  - **Kubernetes** — `KubernetesRuntime` port-forwards the service (#108) and
    injects the resolved URL.
  - **Cloud** — read-only CSPM against a live account (#133).
- **build→deploy→test orchestration** (#233): declare `build:` steps
  (`docker build` / `npm run build`) + a `docker_run` target; the Evaluator runs
  the build before the lanes, then `docker run`s the image, health-polls,
  injects `TFACTORY_TARGET_URL`, runs the lane, and tears the container down.

## 4. Connecting to environments

Targets are declared in `.tfactory.yml`:
- `http` (`base_url` + optional `health_check` + `auth`)
- `docker_compose` (`compose_file` + `wait_for`)
- `kubernetes` (`service` + `port_forward`)
- `cloud_provider` (provider + regions + scan)

URL resolution precedence (#234): `TFACTORY_TARGET_URL` env override (CI injects
a freshly-deployed URL) → target `base_url` → k8s port-forward at runtime →
best-effort ingress discovery (`kubectl`). A configured `health_check` is
**probed before the lane runs** — a down target reports "target unhealthy"
(status `target_unhealthy`) instead of an opaque timeout.

**Credentials:** the broker resolves `env:` / `store:` / `vault:` refs into
ephemeral, read-only, post-run-wiped env/files; secrets are never inlined.
**Authed browser/api lanes:** a Playwright `storageState` login fixture is
auto-generated (`scaffold_auth_setup`, single-step + SSO) so the login runs once
per run and is reused (#107 / #235).

## 5. Scoring + confidence

The Evaluator computes **5 signals** per test → categorical verdict
(`accept` / `reject` / `flag`): coverage delta · 3× stability · mutation ·
flake-lint · LLM semantic relevance. Cross-run **flaky-history** (flip-rate) is
authoritative (#239): a FLAKY test is demoted `accept → flag`.

**CI parity (#302) — green that doesn't lie.** Borrowed from the Hermes agent's
operating contract, a sixth signal guards against tests that pass locally but
fail in CI. Two facets, surfaced as `signals_summary.ci_parity`:

- **Env parity** — the pytest lane grades under a CI-matching environment
  (ambient credentials blanked, `TZ=UTC`, `PYTHONHASHSEED=0`, locale
  normalised) on top of the `--network=none --read-only` sandbox, so a test
  silently leaning on a developer's creds or timezone fails here the way it
  would in CI. Owned by `DockerRunner.run_pytest` (`ci_parity_env()`);
  disable globally with `TFACTORY_CI_PARITY=0`.
- **Real imports** — a static AST check: a suite whose pass depends on
  `mock.patch()`-ing out the *subject module under test* (and never importing
  it) is grading a fake. Such a test is demoted `accept → flag`
  (`ci_parity: mocked-subject`). Conservative — it never fires when the
  subject is genuinely imported (even alongside a collaborator patch), nor on
  generic/unresolved targets.

The status (`yes` / `mocked-subject` / `no`) rides into the triage report's
per-test signal line and the JSON candidate.

A deterministic **numeric confidence** in `[0,1]` (#238) is derived from the
weighted signals (renormalised over present signals; flaky-penalised), stamped
on each verdict's `signals_summary.confidence` plus a run-level
`confidence_summary` (`mean`, `accepted_mean`, `commit_readiness`). A golden
corpus guards against scoring drift.

## 6. Verification in the repo + Backstage

- **Repo:** the Triager writes `findings/triage_report.{md,json}`, and
  (dry-run by default) commits accepted+flagged tests + posts a PR comment.
  The report header carries the SUT component ref (`_Covers: …_`).
- **Backstage:** on terminal status the Triager emits a per-component
  test-quality fact (accept-rate, confidence rollup, flaky count) to
  TechInsights (#240) — opt-in via `TFACTORY_BACKSTAGE_TECHINSIGHTS_URL`.
- **Badge:** public `GET /api/badges/<project>/<spec>/test-acceptance.svg`
  (#241), accept-rate coloured by commit-readiness, embeddable in README.

## 7. Security model

Sandbox (`--network=none` default, RO mounts, no docker.sock, ephemeral creds),
egress gating, constant-time + rate-limited inbound webhook, opt-in signed
commits, dry-run-by-default side-effects. Full controls + per-release checklist:
`guides/security-hardening.md`.

## 8. Toward 100% automation — remaining gaps

- artifact/image-digest pinning (test an exact digest, not just a tag)
- more compiled languages (C# / Go / Rust); Java JaCoCo coverage into the
  Evaluator delta + live PIT validation
- generated JUnit templates + a Java preflight/flake-lint (parity with Py/TS)

(shipped this epic: build→deploy→test orchestration #233, Cypress/Vitest #236,
Java lane wedge #237, confidence #238/#239, Backstage #240/#241, health-gate
#234, storageState #235, hardening #242)
- (shipped this epic: confidence #238/#239, Backstage #240/#241, health-gate
  #234, storageState #235, hardening #242)

See epic [#232](https://github.com/olafkfreund/TFactory/issues/232).
