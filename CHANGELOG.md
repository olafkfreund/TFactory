# Changelog

## 0.11.0 — enterprise test frameworks: Karate, Selenium, Cucumber (2026-06-19)

- **Three new framework descriptors** (per `docs/plans/2026-05-28-enterprise-test-frameworks-design.md`), registered + validated by the framework registry so the planner can detect/select them and Gen-Functional has a context block:
  - **Karate** (`frameworks/karate/`) — JVM API-testing DSL, API lane, single-artifact `.feature` files (built-in step defs); reuses the `tfactory-runner-java` image (Maven fetches the karate dep per-project). `coverage_strategy: skip`.
  - **Selenium** (`frameworks/selenium/`) — Python browser lane; new `tfactory-runner-selenium` image (Python + selenium 4 + chromium + matching chromedriver). The context block mandates explicit `WebDriverWait` (Selenium has no auto-wait — the #1 flake source). **Image built + a real headless browser run validated locally.**
  - **Cucumber** (`frameworks/cucumber/`) — cucumber-js BDD overlay, browser lane; new `tfactory-runner-cucumber` image (Playwright base + @cucumber/cucumber). The context block requires emitting BOTH the `.feature` and the matching step definitions (the two-artifact model).
- **Runner images** added to the `runner-images.yml` build matrix (`selenium`, `cucumber`) with hello-world smokes; Karate rides the existing `java` image.
- Registry test covers the three new descriptors (language/lanes/context guarantees).

## 0.10.1 — equivalence lane: in-cluster k8s-Job backend (RFC-0010) (2026-06-19)

- **In-cluster execution backend for the equivalence lane.** `agents/equivalence_lane.py` gains `_kube_oracle_runner` (runs the oracle harness as an ephemeral Kubernetes Job via `KubeJobSandbox` — the pods have no container runtime) selected by `TFACTORY_EQUIVALENCE_BACKEND=kube` (default `docker`). The small source tree + harness + vectors are embedded base64 in the Job command. **Smoke-validated on a live k3d cluster**: faithful rewrite 100% parity / PASS, buggy `fee()` 67% / FAIL. `scripts/demo_equivalence.py --kube` runs it end to end.
- **Robust result parsing.** `_parse_results` now falls back to a Python-literal parse when JSON decode fails — some k8s pod-log clients re-serialise captured stdout with single quotes; the data is identical and must still parse.

## 0.10.0 — behavioral-equivalence lane for language migrations (RFC-0010) (2026-06-18)

- **Differential/equivalence verification for a language rewrite ([RFC-0010](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0010-code-aware-planning-and-behavioral-equivalence.md), Factory#105).** `agents/equivalence_runner.py` is the pure core: it compares the new implementation against the legacy reference oracle over the golden corpus with numeric tolerance, order-sensitive structures, strict bools, and a cross-language error-class map (Python `ValueError` ≡ Rust `InvalidInput`). A `ParityReport` yields `parity_ratio`, per-vector verdicts, and an **honest claim** — partial parity, critical-vector divergence, and uncovered modules can never read as full equivalence.
- **Live execution (`agents/equivalence_lane.py`).** A language-neutral harness protocol (read JSON input vectors → emit JSON results); the Python oracle harness is auto-generated and run in the hardened DockerRunner sandbox (`--network=none --read-only`, `ci_parity_env`) to capture the golden corpus, while the target impl supplies a protocol-conformant `parity_harness`. `run_from_spec()` orchestrates capture → compare → write `findings/golden_corpus.json` + `equivalence_report.json` → merge `equivalence`-lane verdicts into `verdicts.json`. Wired into `run_evaluator` behind the `TFACTORY_EQUIVALENCE_LANE` opt-in flag (best-effort, never fatal to a verify).
- **Honest VAL-2.** The `equivalence` lane maps to VAL-2 in `val_block`; reject verdicts on partial parity fail VAL-2 and the verification gate caps `achieved_level` — equivalence cannot overclaim.
- **Rust mutation.** `agents/lang_rust/mutate_probe.py` (assertion mutation) + `agents/lang_rust/cargo_mutants.py` (full `cargo mutants` campaign, parsed into a strong/weak verdict) wired into `mutation_dispatch` (`rust` added to the supported set).

## 0.9.6 — surface the PARR correlation key so the cockpit renders the test lane (2026-06-14)

- **The `/api/tasks` list now exposes the GitHub issue / correlation key so CFactory can attach a TFactory task to its work item (#377).** The cockpit keys a work item by the GitHub issue number, but the task-list rows built by `load_spec_metadata` (`apps/web-server/server/routes/tasks.py`) never carried it — even though it lives in each spec's `context/source.json` (`issue_number`) and the RFC-0002 task contract (`correlation_key`). So every TFactory task fell back to correlating by its own spec id, landed on a separate work item, and the cockpit's **test-stage lane stayed empty** even while verification ran. Added `_resolve_correlation_issue()` (RFC-0002 contract → `source.json` precedence, mirroring the handback) and populate the existing typed `TaskMetadata.githubIssueNumber`. No CFactory change needed. 8 new tests.
- **Pin `fastapi==0.136.3` / `starlette==1.3.1`.** `0.137.0` broke route introspection (`prometheus get_route_name` → `_IncludedRouter` has no `.path`, 500s every `/api` route); pinned alongside the fix and later applied to AIFactory and PFactory.

## 0.9.5 — RFC-0001a evidence gate on the completion outcome; opt-in review lane; subtask lane/timing on the API (2026-06-14)

- **RFC-0001a evidence gate on the completion outcome (#373).** The normalized
  completion envelope no longer reports a green `outcome` for a verify that
  evaluated nothing. `apps/backend/agents/triager.py` (`_build_completion_envelope`)
  now downgrades a `triaged` status with **no actionable evidence** to
  `outcome="failure"` with `halt_reason="no_evidence: verify produced no
  verdicts"`. Actionable evidence is any real verdict produced — evaluated
  (`verdicts_count > 0`), accepted (`committed_count > 0`), or flagged
  (`flagged_count > 0`). An additive `evidence` block (`proof_kind: "tests"`,
  `verdicts`, `accepted`, `flagged`, `rejected`) rides on every envelope.
  Note: all-tests-flagged still counts as a non-failure outcome — `flag` means
  "needs human attention" by design and drives the hand-back loop. The gate only
  rewrites the normalized `outcome`; TFactory's internal `status` (its state
  machine + hand-back read it) is left unchanged.
- **Opt-in review lane — LLM staff-engineer code review (#371/#372).** A new
  analysis lane (`apps/backend/agents/review_lane.py`, persona prompt
  `prompts/review_lane.md`, adapted from the vendored `code-reviewer` agent) runs
  a reviewer over the build's changed code and writes `findings/review.json`. It
  is **OFF by default**, gated by `TFACTORY_REVIEW_LANE=1`; when enabled it runs
  in parallel with the Evaluator. It is additive and complementary: it is **not**
  part of the 5-lane test-runner spine (unit/browser/api/integration/mutation) and
  never touches the Evaluator/Triager/verdict contract or blocks the verify path.
- **Subtask lane + timing on `/api/tasks/{id}` (#374).** Subtasks now expose
  `lane` (the v0.2 test lane: unit/browser/api/integration/mutation), `started_at`,
  and `completed_at`; the subtask `status` widened to a string so lane states like
  `stuck`/`blocked` round-trip. These feed CFactory's test-stage lane-pipeline
  execution diagram. All three fields are additive + optional and tolerate absence
  on lane-untagged plans (`apps/web-server/server/routes/tasks.py`).

## 0.9.4 — security hardening: SSRF guard, fail-closed auth bind, CI injection fix; envelope single-source-of-truth (2026-06-13)

- **SSRF guard on the network-enabled test lanes (#361).** The browser / api /
  integration lanes take their target URL from the (attacker-influenceable) AIFactory
  handoff and feed it to a health-poll and the test container. New
  `apps/backend/tools/runners/net_guard.py` resolves the target host and **blocks**
  cloud-metadata / link-local (`169.254.0.0/16`, `fe80::/10`) and IPv6 unique-local
  (`fc00::/7`) *unconditionally*, loopback unless `allow_loopback=True` (AppRuntime's
  compose health-poll opts in; the handoff URL does not), and RFC-1918 unless
  `allow_private=True`. Every DNS-resolved address is checked, so a mixed
  public/internal answer still trips it. Closes the IMDS-reachability path on the
  network lanes.
- **Fail-closed auth bind (#361, harness exemption #362).** `apps/web-server/server/config.py`
  gains a startup guard that **refuses to boot** when `DISABLE_AUTH=true` while `HOST`
  is not loopback (an unauthenticated control plane on the network), unless an explicit
  escape-hatch env var is set. `#362` exempts a live pytest run (keyed on
  `PYTEST_CURRENT_TEST`, never present in a real deployment) so CI's trusted-sandbox
  bind on `0.0.0.0` still works — the guard still protects production unchanged.
- **GitHub Actions script-injection fix (#361).** `tfactory-dispatch.yml` interpolated
  the untrusted issue title straight into a `curl` JSON body; it now moves to an `env:`
  block (`ISSUE_TITLE`) referenced as a shell variable, so a `$(...)`/backtick title
  can't execute in the runner.
- **Completion-envelope schema version: one source of truth (#363, tracking #360).**
  The `schema_version` lived in both the vendored JSON schema `$id` and a Python literal
  that could silently drift. `apps/backend/agents/completion_schema.py` now parses the
  version from the schema `$id` at import time; both `apps/backend` (Triager/producer)
  and `apps/web-server` (relay) read that single constant. A test asserts schema `$id`,
  schema `title`, and the runtime constant agree, so drift is structurally impossible.
- **Begin decomposing the `routes/tasks.py` god-file (#364, tracking #360).** Extracted
  the IDE/terminal launcher routes into `apps/web-server/server/routes/worktree_tools.py`.
  No behaviour change.
- **Program CI: post-deploy PARR seam-gate, re-engineered (#367).** The cross-repo
  reusable-workflow form failed the Deploy workflow at startup; reverted (#366) and
  re-landed as a steps-based soft gate inside the deploy job (`deploy.yml`).

## 0.9.3 — ingest auto-runs the planner: pin agents.planner at startup (#347) (2026-06-11)

- **`/api/specs/ingest` now reliably auto-schedules the Planner (TFactory #347).** Ingested specs were landing at `status=pending` with `planner_scheduled: false` and the warning "planner module not importable" — but only in the long-lived server process. The request-time lazy import `from agents.planner import schedule_planner` (inside `create_spec_ingest_workspace`) intermittently raised `ImportError` mid-request, while the very same import resolved cleanly in every fresh process and at app startup. Fix: `routes/specs.py` now imports `agents.planner` once at module load (app boot), pinning it into `sys.modules` so the downstream lazy import is a fast cache hit instead of a fragile fresh import. This unblocks the AIFactory→TFactory verify leg of the PARR loop (the contract already arrives; now the test pipeline actually runs).
- **Planner scheduling failures are no longer swallowed.** Both `schedule_planner` call sites in `task_control.py` now catch `Exception` (not just `ImportError`), log the traceback, and surface a `planner scheduling failed: …` warning — so a future regression shows up in logs and the ingest response instead of a silent `pending`.

## 0.9.2 — contract-carrying handoff: test the declared ACs, not inferred ones (2026-06-10)

- **`/api/specs/ingest` now accepts the signed Task Contract and uses it as the authoritative test profile (#71 Phase 3, with AIFactory v3.6.14).** The handoff was contract-less (`{project_id, spec_id, spec_text}`), so the Planner inferred lanes/frameworks and PFactory's declared `tfactory` block (lanes/frameworks/`ac_to_code_map`) was discarded. `SpecIngestRequest` now carries an optional `contract`; `create_spec_ingest_workspace` persists it to `context/task_contract.json` (where `read_task_contract()` looks first) when it has the RFC-0002 markers, so `parse_tfactory_profile` drives lane/framework selection. No contract → inference, unchanged (backward compatible).

## 0.9.1 — fix AIFactory→TFactory spec ingest project resolution (2026-06-10)

- **`/api/specs/ingest` now resolves projects from the web-server store by id OR name (AIFactory #517).** It resolved via the agent-tools file store (`~/.tfactory/projects.json`), which is empty/diverged from the DB-backed store `/api/projects` uses — so every AIFactory→TFactory handoff 404'd. Now uses the same `load_projects()` source and matches by id or name (AIFactory sends the project name).

## 0.9.0 — enterprise foundation: PR gate · generic ingestion · tenant hygiene · Java coverage (2026-06-10)

> First slice of the enterprise 90-day plan (`.agent-os/product/enterprise-90day-plan.md`),
> plus the per-user token + Ollama Cloud groundwork.

- **PR-native quality gate (WS1, #310–#312).** The Triager can publish a GitHub
  commit status (`TFactory / tests`) that passes/fails a PR against a configurable
  policy (`agents/quality_gate.py` + `tools/pr_status.py`), opt-in via the
  `.tfactory.yml` `quality_gate` block + `TFACTORY_PR_STATUS=1`. See `guides/pr-gate.md`.
- **Run TFactory without AIFactory — generic spec ingestion (WS2, #313–#315).**
  `create_spec_ingest_workspace` + the `task_create_from_spec` MCP tool +
  `POST /api/specs/ingest` (with a typed `ingestSpec` client) turn a raw
  markdown/Gherkin/EARS spec into a native test-gen task — no branch required.
- **GitHub issue → native TFactory test task (#326/#327).** The portal's
  issue→task flow now creates a native test-generation task via the ingest
  endpoint, not an inherited AIFactory coding task.
- **Tenant hygiene — projects toward org-scoped DB (WS3, #316–#319).** A
  `projects.json`→DB migration into the owner's Personal org, a `ProjectStore`
  abstraction (JSON default / org-scoped DB behind `APP_PROJECTS_BACKEND`), a
  request→org resolver, and the live projects route routed through the store seam.
- **Java coverage wired into the Evaluator (WS4, #320).** Format-aware coverage
  parsing dispatches JaCoCo for the Java lane (Cobertura stays the default).
- **Per-user `acw_` API tokens on the REST surface (#305).** A user-minted key
  with the `api:full` scope authenticates on `/api/*` (for the handover skill +
  CLI), with expiry enforcement + `last_used_at` tracking.
- **Ollama Cloud documented + verified (#306).** Reachable via the
  `openai-compatible:<model>` path + `OPENAI_COMPATIBLE_*` env; connectivity
  checker `python -m providers.ollama_cloud_check`. See `guides/ollama-cloud.md`.

## 0.8.2 — bash sandbox toggle for k3d (2026-06-10)

- **Agent bash no longer breaks under the OS sandbox on k3d (AIFactory #363).** Mirrors AIFactory v3.6.9: bwrap can't mount `/proc` on k3d (the node is a container), so the SDK's bash sandbox broke every agent command. Gated `sandbox.enabled` behind `AIFACTORY_BASH_SANDBOX` (default on); set `false` on the cluster — bash works, isolation via the K8s pod boundary + command allowlist until gVisor lands.

## 0.8.1 — agent sandbox deps (bubblewrap + socat) (2026-06-09)

- **Agent command sandbox now actually engages (#321).** The Chainguard runtime
  image omitted `bubblewrap` (`bwrap`) and `socat`, so the Claude Agent SDK logged
  *"Sandbox disabled: … bubblewrap (bwrap) not installed"* and ran agent bash
  commands with **no** filesystem/network enforcement — unacceptable for a tool
  that runs generated tests sandboxed. Both are now installed in the runtime
  `apk` layer. Verified on the cluster: the node allows unprivileged user
  namespaces, so `bwrap` creates a real sandbox (not just silencing the warning).

## 0.8.0 — Factory PARR spine + CI-parity verification (2026-06-09)

> TFactory stops being a standalone tool and becomes a **verified node in the
> [Factory](https://factory.freundcloud.com/) line** — it picks up governed test
> targets from PFactory, emits a shared completion event CFactory can watch, and
> registers itself in the Backstage software catalog. Part of the Factory
> PARR-spine epic (Plan · Act · Review · Report).

- **CI-parity verification signal — real imports, not mocks (#302).** A sixth
  Evaluator signal, inspired by the Hermes agent, that guards against "green
  that lies" — tests passing locally (against mocks or a developer-shaped env)
  yet failing in CI. The pytest lane now grades under a CI-matching environment
  (ambient credentials blanked, `TZ=UTC`, hash seed pinned, locale normalised)
  on top of the existing `--network=none --read-only` sandbox, and a static
  "real-imports" check flags any suite passing only by mocking out the subject
  module under test (`signals_summary.ci_parity: mocked-subject` → demote
  `accept`→`flag`). Surfaced in the verdict + triage report; documented in
  `guides/testing-model.md`.
- **PFactory governed-target pickup (#193–#197).** TFactory now consumes test
  work that [PFactory](https://pfactory.freundcloud.com/) has planned and
  governed, rather than only ad-hoc handovers:
  - **Recognise + enqueue (#195).** Detect governed test targets and enqueue
    them through the existing task pipeline.
  - **Tag taxonomy (#194).** Create the shared label set PFactory and TFactory
    agree on for routing governed work.
  - **`pfactory:meta` oracle (#196).** Parse the `pfactory:meta` block as the
    test oracle — the governed acceptance contract the generated tests must
    satisfy.
  - **Generate · run · report back (#197).** Produce tests for a picked-up
    target, execute them, and report the result back up the spine.
- **RFC-0001 normalized completion event (#198 / #211 / #214 / #224).** The
  Triager's terminal-status completion event now conforms to
  **[RFC-0001](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0001-correlation-key-and-completion-event.md)**
  — the canonical cross-service correlation-key + completion-event schema, so
  one shape flows across AIFactory · PFactory · TFactory and CFactory watches a
  single contract:
  - Shared **`correlation_key`** (GitHub issue number, with a synthetic
    `tf-<spec_id>` fallback so it is never null); legacy int `correlation_id`
    retained as a back-compat alias.
  - Default integration port moved **3102 → 3103** (#198); MCP-proxy tests
    realigned.
  - Triager emits an **RFC-0001 usage block** on completion events (#224).
  - See `docs/completion-event-envelope.md`.
- **Backstage software-catalog onboarding (#215 / #216 / #223).** TFactory is now
  importable into Backstage: `catalog-info.yaml` + TechDocs (#215/#216),
  completion-event TechDocs aligned to RFC-0001, and enriched catalog
  annotations + an AI-assistant skill descriptor (#223).
- **Reach more systems under test.** Multi-step / SSO test-target login flows
  (#107) and `toHaveScreenshot` visual baselines wired to the portal-managed
  store (#109); live Kubernetes port-forward dispatch fix (#108).
- **Hardening.** Patched the binutils CVE (CVE-2026-6846) to clear the P0 Trivy
  gate; web-server deps installed into the backend venv so the full suite imports
  cleanly (#219); core pipeline agents decomposed per a Clean Code review;
  pre-existing ruff lint debt cleared. Backend suite now at **2803 tests**.

## 0.7.0 — Reliable completion delivery, SSO fix, SaaS connectors (2026-06-08)

> Hardens the Factory PARR spine's completion-event delivery (epic #284, TFactory
> side complete), fixes the OIDC SSO login loop, and rounds out the SaaS connector
> support.

### ✨ New Features

- **Completion outbox + retrying relay (#281).** At-least-once delivery of
  RFC-0001 completion events — the Triager durably enqueues the envelope before
  delivery; a relay drains it with exponential backoff + dead-lettering and
  replays across restarts. Opt-in via `TFACTORY_COMPLETION_OUTBOX` /
  `APP_COMPLETION_RELAY_ENABLED`.
- **Additive envelope upgrade (#282).** Per-event `id` (idempotency key),
  CloudEvents-core (`specversion`/`source`/`type`/`time`), and W3C `traceparent`
  ride alongside the legacy fields. Published schema + CI validation.
- **Typed handback contract + bounded retry (#283).** Versioned triage-report
  contract (`contracts/handback-triage-contract.v1.schema.json`), assertion
  pinning (manifest hash + additive-only diff-gate), and a terminal `needs_human`
  completion event when the correction-cycle cap is reached.
- **SaaS connector visual lane (#173).** Opt-in `visual: true` browser lane on
  `ConnectorTarget`/`HttpTarget` for visual-inspection runs, with ServiceNow
  browser guidance (`iframe#gsft_main` + stable selectors).
- **SAP OData connector (#111).** `sap-odata.py.tmpl` api-lane check (OData v2/v4,
  bearer/OAuth or Basic) — all four connector platforms now have library checks.

### 🐛 Bug Fixes

- **OIDC/Keycloak SSO login loop (#286).** Honor the `access_token` cookie in the
  auth middleware + `get_current_user`, and make the SPA `checkAuth` cookie-aware
  so SSO logins no longer bounce back to `/login`.
- **binutils CVE-2026-6846 (#218).** Pinned `binutils>2.46-r1`; Trivy P0 gate green.

### 🔧 Other

- Login page auto-versions from `package.json` at build time.

## 0.6.0 — GitHub Agentic Integration (2026-06-08)

> TFactory can now route tasks to **GitHub Models** (OpenAI-compatible inference
> via `GITHUB_TOKEN`) and dispatch test-writing to the **GitHub Copilot cloud
> agent** (`copilot-swe-agent[bot]`). A new **MCP HTTP endpoint** (`POST /mcp`)
> exposes six TFactory tools so Copilot can read test plans, AC maps, coverage,
> and results, and report back. Three **GitHub Actions workflows** wire the
> `tfactory:run` label trigger, Copilot PR auto-test, and PR coverage comments.
> Epic #277 — closes #278, #279, #280.

- **GitHub Models provider routing (C1).** `github-models/<catalog-path>` model
  strings route to the OpenAI-compatible provider with
  `https://models.github.ai/inference` as base URL and `GITHUB_TOKEN` for auth.
  Factory alias `github-models` added to provider registry.
- **Copilot cloud agent dispatch (C2).** `agents/copilot_dispatch.py` assigns
  `copilot-swe-agent[bot]` to a GitHub issue with a structured test-suite prompt,
  polls for a Copilot-authored PR (59-minute timeout), and persists
  `copilot_dispatch` metadata to `test_task_metadata.json`.
- **MCP Copilot HTTP endpoint (C3).** `POST /mcp` (JSON-RPC 2.0) exposes
  `tfactory_get_test_plan`, `tfactory_get_ac_map`, `tfactory_get_coverage`,
  `tfactory_get_results`, `tfactory_get_spec`, and `tfactory_report_result`.
  Bearer auth via `COPILOT_MCP_TFACTORY_TOKEN`.
- **GitHub Actions workflows (C4).** `tfactory-dispatch.yml` fires on
  `tfactory:run` issue label; `copilot-pr-test.yml` triggers TFactory on
  Copilot-authored PRs; `pr-review-tests.yml` posts coverage comments on all PRs.

## 0.5.0 — Bidirectional AIFactory ↔ TFactory integration (2026-06-03)

> Close the loop with AIFactory: when TFactory's tests find problems, hand a
> **correction** back to AIFactory for a fix, then re-test — bounded so it can't
> run away. The reverse of `/handover-to-tfactory`. Epic
> [#182](https://github.com/olafkfreund/TFactory/issues/182).

- **Hand-back pipeline (#182).** New `agents/handback/` packages a finished
  run's failures into a `QA_FIX_REQUEST.md`-shaped correction and (opt-in) sends
  it to AIFactory's QA Fixer:
  - **Traceability (#183).** The snapshotter records the AIFactory hand-back
    target (`aifactory{project_id,spec_id,api_url,task_id}` + `correction_cycle`)
    in `context/source.json`; `api_url` defaults to AIFactory's web-server
    (`http://localhost:3101`, override `TFACTORY_AIFACTORY_API_URL`).
  - **Builder + renderer (#184).** `request.py` + `render.py` — pure-compute
    selection of failing tests → a deterministic fix-request payload (reuses the
    triage report + the visual correction plan).
  - **Sender + Triager hook (#185).** `send.py` writes
    `findings/handback_request.{md,json}` always and POSTs only on
    `dry_run=False AND confirm`; the Triager's terminal-status hook *prepares*
    (default ON) and *sends* on opt-in (`TFACTORY_HANDBACK_SEND=1`), mirroring
    `TFACTORY_TRIAGER_GIT_WRITE`.
  - **Operator skill + CLI (#186).** `/handback-to-aifactory` (+ AIFactory
    companion) previews then sends via the AIFactory MCP tool
    `task_apply_correction`, or `python -m agents.handback <spec_dir> --send`.
  - **Bounded closed loop (#187).** `loop.py` + `/tfactory-fixloop` drive a
    test→fix→re-test cycle that stops at **passed**, or **stuck** (cap
    `TFACTORY_HANDBACK_MAX_CYCLES` default 2, or no progress).
  - **AIFactory receiver** (sister repo, `AIFactory#317`): `POST
    /api/tasks/{task_id}/apply-correction` + MCP `task_apply_correction` write
    `QA_FIX_REQUEST.md` onto the original spec and run the existing QA Fixer.
  - Dry-run-first + opt-in throughout (no automatic pushes). See
    `guides/aifactory-handback.md` and
    `docs/plans/2026-06-03-aifactory-tfactory-handback-design.md`.

## 0.4.0 — Visual Inspection Run + SaaS connector targets (2026-06-03)

> A new **Visual Inspection Run** feature (all no-tenant phases shipped) plus the
> first-class **SaaS connector** target. Epic
> [#170](https://github.com/olafkfreund/TFactory/issues/170).

- **Visual Inspection Run (#170).** Record a generated Playwright **browser** run
  (trace + video + step-labeled verification *and* error screenshots) and package
  it into `automated-test/<YYYY-MM-DD-HHMMSS>/` with a deterministic human report,
  an LLM correction plan (injectable seam + deterministic fallback), a GitHub
  issue export (dry-run), and `meta.json`. New `agents/visual_inspection/`
  (`packager` · `report` · `correction_plan` · `issues` · `store`); a
  `write_paths_to_branch` git helper to commit the folder to the SUT repo
  (dry-run default); a portal **Visual Reports** page + `/api/visual-inspections`
  routes; and a `/handover-to-tfactory` opt-in (`visual_inspection {enabled,
  target, flow}` threaded through `task_create_and_run`). Phases P1/P2/P4/P5
  shipped; **P3** (ServiceNow browser/SSO) remains, needing a live tenant.
  See `docs/plans/2026-06-03-visual-inspection-run-design.md`.
- **SaaS connector target (#111).** A first-class `type: connector` target
  (ServiceNow / Salesforce / SAP / MuleSoft) reusing the http + credential-vault
  auth, plus a platform registry mapping each platform → API style · `library/`
  check template · guidance. See `guides/saas-connectors.md`.
- **storageState login-once scaffolding (#107).** Gen-Functional scaffolds
  `auth.setup.ts` + a `requires_auth` Playwright config from a ref-auth target's
  selectors, so a browser test logs in once and reuses the session.

## 0.3.0 — Cloud testing (AWS/GCP/Azure), platform foundations + portal redesign (2026-06-03)

> The cloud epic ships end-to-end across three providers, four backlog
> capabilities get their foundations wired + tested, and the portal gets a
> flagship-grade design pass. Headlines below; the credential-broker fast-follow
> work is folded in from the prior Unreleased section.

- **Cloud infrastructure testing — AWS · GCP · Azure (epic #133, complete).**
  A read-only assessment flow: access gate → discovery (host `aws`/`gcloud`/`az`)
  → Mermaid topology → Prowler/CIS scan (OCSF) in `tfactory-runner-cloud` →
  accept/flag/reject verdict → downloadable remediation plan. GCP uses ADC,
  Azure uses `--az-cli-auth` (the image bundles `azure-cli`); all three
  live-verified read-only against real accounts. `frameworks/cloud-discover` +
  `frameworks/cloud-prowler` descriptors + a high-signal check library (#138).
  Launch from the portal — **+Task → Cloud Infrastructure** runs the discovery
  gate first, then backgrounds the assessment; reports land in **Cloud Reports**.
  See `guides/cloud-testing.md`.
- **Test-target login credentials wired into the lanes (#107).** A
  `auth: {type: ref}` target's credential is resolved and injected
  (`TEST_USERNAME`/`TEST_PASSWORD`) into the api/browser run, egress-gated and
  wiped after — so a generated test can log in to a SUT, then test it.
- **Kubernetes port-forward dispatch wired into the Evaluator (#108).** A
  `type: kubernetes`, `port_forward: true` target is `kubectl port-forward`-ed
  for the run lifetime (auth via the read-only kubeconfig), its
  `http://localhost:<port>` injected as `TFACTORY_TARGET_URL`, torn down on
  success and failure.
- **Visual-regression baselines surfaced in the portal (#109).** A portal API
  over `agents.evidence.visual_baseline` — list / serve / accept (traversal-
  guarded) — backing the Playwright `toHaveScreenshot` template + the
  threshold/anti-flake guidance already shipped.
- **Portal redesign.** Flagship-grade pass: home Tests list + Cloud Reports as
  card layouts with humanised time and verdict-driven status colour; a branded,
  atmospheric login; a grouped top-bar status cluster; the new Gruvbox flask
  favicon; and a fix for the New Task dialog's leftover Ocean-theme colours.

- **Fix: Evaluator no longer fails on a verdicts.json with trailing data.** The
  LLM sometimes wraps the JSON in a ```` ```json ```` fence or appends a
  sentence after it, which made strict `json.loads` raise *"Extra data"* and
  the task land in `evaluator_failed` / `evaluator_invalid_verdicts`. The
  validator now parses leniently (fence-strip + `raw_decode` of the first JSON
  value) and rewrites the salvaged object so the Triager reads clean JSON.

- **Workload-identity federation (#74).** Mint short-lived scoped credentials
  from an OIDC token via a `wif` block in `~/.tfactory/credentials.json`. AWS
  STS `AssumeRoleWithWebIdentity` is implemented (`tfactory_secrets/wif.py`);
  `resolve_cloud("aws")` returns short-lived keys that the broker caches with
  their TTL and re-mints near expiry. GCP WIF / Azure federated tokens routed
  (fast-follow). Completes the Credential Broker epic (#62).
- **Sandbox credential injection (#73).** Network-enabled lanes (api /
  integration) can authenticate inside the Docker sandbox — broker-resolved
  cloud tokens as env vars plus a kubeconfig bind-mounted read-only — gated by
  `network != none` **and** egress opt-in. The unit lane (`--network=none`)
  gets nothing; mounted secret files are wiped after the run. Seam:
  `tools/runners/sandbox_credentials.py` + `DockerRunner.run(secret_files=…)`.
- **Operator credential config (#71).** Formalised the
  `~/.tfactory/credentials.json` (0600) schema/loader
  (`tfactory_secrets/operator_config.py`): a `cloud` block (provider → backend
  ref) plus a `credentials` block of named sets, the host-wide analogue of the
  per-project `.tfactory.yml` `credentials:` (project wins on collision).
- **Triager completion callback (#85).** When a task reaches a terminal
  status, the Triager can notify a watcher so `/tfactory-watch` needs no
  polling. Two opt-in, best-effort channels (both OFF by default; a failing
  target never breaks the pipeline): `TFACTORY_COMPLETION_WEBHOOK=<url>`
  (POSTs `{task_id, project_id, status, phase, updated_at}`) and
  `TFACTORY_COMPLETION_SENTINEL=1` (writes `findings/COMPLETED.json`).

## Unreleased — Credential Broker (epic #62)

> Agents can now authenticate to cloud environments using vault-backed or
> locally-encrypted credentials, gated by an honest egress posture. See
> `guides/credentials.md` and `docs/plans/2026-05-30-credential-broker-design.md`.
>
> **Pluggable secrets backends** (`apps/backend/tfactory_secrets/`). A
> `SecretsBackend` abstraction + factory (mirrors `providers/factory.py`) +
> ref routing (`env:` · `sops:`/`age:`/`agenix:` · `vault:` · `azurekv://` ·
> `aws-sm://` · `gcp-sm://`). Cloud SDKs import lazily — an absent package makes
> only that backend unavailable. (#63 #64 #66 #67 #68 #69)
>
> **CredentialBroker** (#65) extends the existing `core/mcp_credentials.py`
> chain with a vault-fetch head, materialises file creds (kubeconfig, GCP ADC)
> at **0600** in a per-task scratch dir, **wipes** them on task end, and wires
> resolved env into the agent via `core/client.py` (off unless egress is on).
>
> **Honest egress** (#8): `.tfactory.yml` `egress.enabled` gate (default OFF) +
> `credentials:` block; a secret-free egress **manifest** + badge; log redaction;
> `python -m tfactory_secrets.cli audit|doctor|resolve`.
>
> Fast-follows: sandbox-test injection (#73), OIDC/workload-identity
> federation (#74). ~99 new tests across the secrets suite.

## 0.2.2 — Multi-provider hardening: Codex auth + GitHub Copilot CLI (2026-05-30)

> Makes the non-Claude provider lanes reliable for demos and BYO-LLM.
>
> **Codex now works regardless of your global `codex login`.** A bare
> ChatGPT-account login rejects every model ("model is not supported when
> using Codex with a ChatGPT account"), which silently broke TFactory's Codex
> lane. `codex_agentic.py` now provisions a TFactory-owned `CODEX_HOME`
> (`~/.tfactory/codex-home/`) with an api-key `auth.json` built from
> `OPENAI_API_KEY` and points the `codex mcp-server` subprocess at it — your
> global login is left untouched. It also surfaces MCP run errors instead of
> swallowing them as "(no output from Codex MCP)", resolves the `codex` shell
> alias to `codex-cli`, and defaults to `gpt-5.3-codex`. Verified end-to-end:
> Planner emits a valid plan even with the broken ChatGPT login still active.
>
> **New provider: GitHub Copilot CLI** (`copilot:<model>`). A first-class
> agentic provider (`copilot_agentic.py`) that runs `copilot -p "<prompt>"
> --allow-all-tools --model <model>` headlessly — Copilot runs its own tool
> loop in one shot. Models: `claude-sonnet-4.5` (default), `claude-sonnet-4`,
> `gpt-5`, billed to your Copilot subscription. Routed via the factory
> (`copilot:gpt-5` correctly resolves to Copilot, not Codex). Verified
> end-to-end: Planner produced a 7-subtask plan on `copilot:claude-sonnet-4.5`.
>
> **Ollama / local models can now write the workspace.** Ollama is the only
> provider whose file ops run through TFactory's own `ToolExecutor`, which was
> sandboxed to a single root (the SUT project dir). The Planner/Gen-Functional/
> Evaluator write into the spec/workspace dir — *outside* that root — so every
> `Write` was denied and the model burned all its turns retrying (looked like a
> convergence failure; it wasn't). `ToolExecutor` now takes `extra_roots`, and
> the spec dir is threaded in for the Ollama provider. Verified:
> `qwen2.5-coder:14b` Planner went from 25-turn timeout to **PASS (3 subtasks)
> in ~36s**.

## 0.2.1 — Version honesty + docs reconciliation (2026-05-30)

> Housekeeping release. No runtime behaviour change.
>
> **Version stamp corrected.** `package.json`, `apps/frontend-web/package.json`,
> and `apps/backend/__init__.py` were still on the inherited AIFactory `3.0.2`
> despite the product being on the v0.x line. Now `0.2.1` — the honest next
> patch after the v0.2.0 release. (Heading uses the bare `## 0.2.1` form that
> `release.yml` validates, unlike the older `## vX.Y.Z` entries below.)
>
> **Docs reconciled to the v0.2 lane spine.** README + CLAUDE.md no longer
> describe the retired v0.1 `Functional / SAST / DAST / Fuzz` lanes; security
> scanning is stated as out of scope (delegated to dedicated pipelines). See
> issues #34 / #35 under epic #33 (Product Hardening & Market Fit).

## v0.2.0 — Enterprise Test Framework Spine (2026-05-29)

> All 16 v0.2 tasks shipped + the deferred Task 16 follow-up (Triager
> evidence-links). Tagged + released:
> <https://github.com/olafkfreund/TFactory/releases/tag/v0.2.0>.
>
> **Backend tests: 1177 passing** (was 531 at v0.1.0-mvp — **+646**).
> **Frontend tests: 49 (LaneStatusGrid) + 187 (TFactoryTaskDetail with
> evidence tab) + the wider suite.**
>
> **Task 16 follow-up (deferred commit 4) landed:** Triager now surfaces
> portal evidence links per accepted/flagged candidate in
> `triage_report.md`, so PR reviewers can click straight from the comment
> to screenshots / video / trace.zip / network.har. Threaded via a new
> optional `spec_dir` param on `build_report`; v0.1 callers
> (`build_report` without `spec_dir`) stay backward-compatible with
> `evidence_urls_by_test_id={}`. Ordering is fixed (screenshots → video →
> trace → network → others by sorted key) so the report stays
> byte-identical for the same input. 7 new tests in
> `tests/test_triage_report.py` covering: empty-evidence v0.1 path,
> spec_dir walk wiring URLs into the report, markdown emoji bullets
> rendered, no-evidence-dir omitted, flagged-yes/rejected-no scope,
> deterministic ordering, ghost-test_id pruned.
>
> **Backend tests: ~1175 passing · Frontend tests: 49 (LaneStatusGrid)
> + 187 (TFactoryTaskDetail with evidence tab) + suite.**

### All 16 tasks closed

| Task | Issue | What shipped |
|------|-------|--------------|
| Task 0  | #16 | Lane spine rename (FUNCTIONAL→UNIT etc.) + frontend reskin |
| Task 1  | #17 | Framework descriptor registry (3 frameworks at MVP, ramp path to 80) |
| Task 2  | #18 | `.tfactory.yml` schema + parser + validator (Pydantic v2) |
| Task 3  | #19 | `.tfactory/tests-catalog.json` schema + atomic IO + 3-step lookup + migration primitive |
| Task 4  | #20 | Snapshotter extension (reads `.tfactory.yml` + tests-catalog into `context/`) |
| Task 5  | #21 | Planner polyglot subtask schema `(language, framework, target_name, intent)` |
| Task 6  | #22 | Gen-Functional generic prompt + per-framework `FrameworkDescriptor.context_block` injection |
| Task 7  | #23 | Per-framework Docker runner images (pytest / Jest / Playwright) + CI workflow |
| Task 8  | #24 | Browser-lane AppRuntime (docker-compose + HTTP HEAD health-poll) |
| Task 9  | #25 | Evaluator per-language primitives — TypeScript `preflight.py` (tsc) / `flake_lint.py` (ESLint) / `mutate_probe.py` (Stryker) |
| Task 10 | #26 | Evaluator coverage adapter (null vs zero for Browser lane per Decision 11) |
| Task 11 | #27 | Triager update-in-place vs create-new vs skip-locked via tests-catalog (3-step lookup) |
| Task 12 | #28 | 15 starter templates (5 each for Playwright / Jest / pytest) + `string.Template` engine |
| Task 13 | #29 | Skills + slash commands — `tfactory-init` / `tfactory-add-test` / `tfactory-from-template` + handover update |
| Task 14 | #30 | Portal endpoints — frameworks / templates / skills / catalog (FastAPI shim pattern) |
| Task 15 | #31 | LaneStatusGrid full reskin (5 independent lanes) + `tfactory init` / `tfactory migrate v0_1_catalog` CLIs |
| Task 16 | #32 | Test evidence capture (screenshots / video / trace / HAR) + retention enforcer + portal endpoint + frontend Evidence tab |

### Task 16 highlights

- **Test evidence capture** — every Browser-lane failure produces
  screenshots + video + trace.zip; every API/Integration test produces a
  network.har. Evidence stored at
  `spec_dir/findings/evidence/<test_id>/`, served via the portal, and
  rendered in a new **Evidence** tab in `TFactoryTaskDetail`.
- **Evidence retention enforcer** — prunes artefacts per configurable
  policy (failures: forever; flagged: 90 days; passing: 7 days; size cap
  per task). Fully injectable `now` parameter for deterministic tests.
- **CatalogEntry evidence fields** — `last_evidence_run_id` and
  `evidence_urls` (via `evidence_urls_raw` + `.evidence_urls` property)
  added to the test catalog; backward-compatible with pre-Task-16 catalogs.
- **EvidencePolicy** in `.tfactory.yml` — concrete typed sub-models for
  `browser:`, `api:`, and `retention:` blocks replace the freeform
  placeholder.
- **Portal evidence endpoint** —
  `GET /api/tfactory/tasks/{spec_id}/evidence/{test_id}/{artifact}` with
  content-type by extension and path-traversal rejection on all three
  path segments.
- **90 new backend tests** across 4 test modules; **8+ new frontend tests**
  in `TFactoryTaskDetail.test.tsx`.

---

## v0.2 — Enterprise Test Framework Spine (in progress)

> Successor to [v0.1.0-mvp](#v010-mvp--walking-skeleton-2026-05-28).
> v0.2 lights the **Browser** lane (Playwright via a docker-compose
> AppRuntime), adds the **API** + **Integration** lanes (HTTP/contract +
> testcontainers), and ships the **framework descriptor registry**,
> **`.tfactory.yml`** target schema, and **`.tfactory/tests-catalog.json`**
> cross-run continuity store. Planner becomes polyglot per-subtask
> (Python+TypeScript at MVP); Gen-Functional generalises via per-framework
> templates + context blocks; Evaluator gains TypeScript primitives
> (tsc / ESLint / Stryker); Triager learns update-in-place vs create-new
> via the catalog; evidence (screenshots / video / trace / HAR) is
> auto-captured and served by the portal for human review.
>
> Driver: `docs/plans/2026-05-28-enterprise-test-frameworks-design.md`
> (11 locked decisions, 80 frameworks catalogued).
> Task plan: `docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md`
> (16 tasks, ~95 commits, ~975 tests at the v0.2 finish line).

### ⚠ BREAKING CHANGE — Lane spine rename (Task 0 / #16)

The `Lane` enum's vocabulary swapped from v0.1's pipeline-stage terms to
v0.2's **modality-based** decomposition (Decision 2):

```
v0.1                       →   v0.2
─────────────────────────────────────────
Lane.FUNCTIONAL            →   Lane.UNIT
Lane.SAST   (out of scope) →   Lane.BROWSER     (new — Playwright)
Lane.DAST   (out of scope) →   Lane.API         (new — HTTP/contract)
Lane.FUZZ   (out of scope) →   Lane.INTEGRATION (new — testcontainers)
Lane.MUTATION              →   Lane.MUTATION    (unchanged)
```

Why: the v0.1 lane names tracked the *security-pipeline* metaphor we
adopted from AIFactory. v0.2's brief (per the user's direction during
the `/super-brainstorm` interview that produced the design doc)
narrowed the product to **functional + feature testing** — security
scanning is owned by separate pipelines / controllers. The new lane
names describe the *modality* of the test (where it runs, what it
exercises), not the pipeline stage.

**Compatibility through v0.2 (removed in v0.3):**

- `Subtask.from_dict({"lane": "functional", ...})` still parses; the
  string is remapped to `Lane.UNIT` with a `DeprecationWarning`.
- `Subtask.from_dict({"lane": "sast", ...})` (and `"dast"`, `"fuzz"`)
  likewise remaps to `Lane.UNIT` with a warning.
- `lane_dispatch.dispatch_lane(lane="functional", ...)` lights the lane
  via the same DockerRunner path used by `"unit"`, also with a warning.
- `lang_registry.get_tool_for_lane(lang, "functional")` returns `None`
  (the registry is keyed on the new lane vocabulary; remap upstream).
- Workspaces created against v0.1 (`status.json["lane_progress"]`
  keyed on `"functional"`) remain readable — the MCP `task_status`
  surface returns the new `"unit"` key but old files lift correctly.

**Frontend:** `LaneStatusGrid` reskinned in lockstep — the lit lane
(was the FUNCTIONAL card showing the current task's status) is now
the **Unit** card; the four placeholder cards relabel to **Browser**
(Phase 2), **API** (Phase 3), **Integration** (Phase 4), **Mutation**
(Phase 5). The component's prop renamed: `functionalStatus` →
`unitStatus`. Full visual polish (icons + colors per lane) lands in
Task 15 (#31).

### Tasks shipped in v0.2

- **#16 Task 0**: Lane rename + breaking-change migration
- **#22 Task 6**: Gen-Functional refactor — generic prompt + context injection

  Gen-Functional is now polyglot. The v0.1 Python+pytest-specific prompt is
  preserved as `prompts/gen_functional-v01-legacy.md` (removed in v0.3). A
  new generic `prompts/gen_functional.md` is parameterized at runtime by the
  framework descriptor's `context_block`. The prompt helper
  (`get_tfactory_gen_functional_prompt`) accepts an optional
  `framework_descriptor` argument (pass `None` for the legacy path; a
  `DeprecationWarning` is emitted). The dispatcher (`gen_functional.py`)
  calls `framework_registry.load_registry()` per subtask, resolves the
  descriptor, and injects it into the prompt assembly. The runner image
  (`DockerRunner(image=...)`) is similarly parameterized by
  `descriptor.runtime.image` instead of the hardcoded
  `tfactory-runner-python:latest`. 25 new tests (57 total across the two
  gen_functional test files).

- **#30 Task 14**: Portal endpoints — framework registry, templates, skills, catalog

  Five new read-only FastAPI routes under `/api/tfactory/`:

  - `GET /api/tfactory/frameworks` — list all registered frameworks (name,
    language, lanes, coverage_strategy, version_range, template_count),
    sorted alphabetically.
  - `GET /api/tfactory/frameworks/{name}` — full `FrameworkDescriptor` as
    JSON. 404 for unknown names; 400 for path-traversal.
  - `GET /api/tfactory/templates?framework={name}` — list templates for a
    framework (name + metadata). 400 if param absent; 404 if framework
    unknown.
  - `GET /api/tfactory/templates/{framework}/{name}` — full template body +
    metadata. 404 if framework or template unknown; 400 for path-traversal
    on either segment.
  - `GET /api/tfactory/skills` — list `.claude/skills/*/SKILL.md` bundles
    with parsed YAML frontmatter. Gracefully returns `{"skills": []}` when
    the directory is absent (Task 13 may not yet be merged).
  - `GET /api/tfactory/tasks/{spec_id}/catalog` — serve the spec's
    `context/tests_catalog.json` snapshot verbatim. 404 if not snapshotted.

  All routes follow the FastAPI-shim-friendly pattern from v0.1's
  `tfactory_tasks.py` (no hard fastapi import; route functions are unit-
  testable without fastapi installed). 127 new tests across four test
  files (`test_tfactory_routes_frameworks.py` · `test_tfactory_routes_
  templates.py` · `test_tfactory_routes_skills.py` + 5 new /catalog
  cases in `test_tfactory_routes_tasks.py`).

- **#31 Task 15**: LaneStatusGrid full reskin + `tfactory init` + `tfactory migrate` CLIs

  LaneStatusGrid receives a complete reskin: all five lane cards (Unit /
  Browser / API / Integration / Mutation) are independently lit by
  `laneStatuses: Partial<Record<LaneId, string|null>>`. Each lane has a
  unique icon (CheckSquare / Globe / Plug / Network / Zap) and accent
  colour (blue / purple / green / orange / red). `TFactoryTaskDetail`
  derives per-lane statuses from `status_json.lane_progress` with v0.1
  compat fallback. `unitLaneState` kept as alias.

  Two new backend CLI commands:
  - `python -m cli init` — interactive scaffolder for `.tfactory.yml` +
    empty `.tfactory/tests-catalog.json`. Non-interactive mode testable
    via flags. Validates output via `load_tfactory_yml()`.
  - `python -m cli migrate v0_1_catalog` — walks
    `~/.tfactory/workspaces/*/specs/*/` and consolidates per-spec test
    entries into per-repo `.tfactory/tests-catalog.json` via the
    existing `tests_catalog.migration.migrate_v0_1_workspace` primitive.
    Dry-run flag prints the plan without writing.

  21 new backend tests (`test_cli_init.py` + `test_cli_migrate.py`);
  23 new frontend LaneStatusGrid tests (49 total in the file).

---

## v0.1.0-mvp — Walking Skeleton (2026-05-28)

> First tagged release. The MVP walking-skeleton pipeline is complete:
> a Claude Code session in an AIFactory repo can invoke
> `/handover-to-tfactory` and the four-agent pipeline (Planner →
> Gen-Functional → Evaluator → Triager) produces a pytest test suite +
> verdicts + a triage report against the AIFactory feature branch.
>
> Functional lane (Python) is active; SAST / DAST / Fuzz / Mutation
> lanes are Phase 2-5 placeholders in the portal.
>
> Tests: **531 backend + 112 frontend = 643 total**, plus a 9-scenario
> manual end-to-end smoke runner (`scripts/e2e-smoke.sh`). Side-effects
> (git commit + PR comment) default to dry-run per the
> "no automatic pushes" policy.

### Tasks shipped (all 12)

  - **#2  Task 1**: Hard fork from AIFactory + scaffold
  - **#3  Task 2**: MCP server + `/handover-to-tfactory` skill
  - **#4  Task 3**: Workspace + snapshotter
  - **#5  Task 4**: Docker runner + lane dispatcher + lang registry
  - **#6  Task 5**: Planner agent (initial + replan + stuck-at-2 transition)
  - **#7  Task 6**: Gen-Functional agent (pre-flight static check +
                    flake-risk lint guardrails)
  - **#8  Task 7**: Evaluator (5-signal verdict pipeline:
                    coverage delta · 3× stability · mutate-and-check ·
                    flake-lint promotion · LLM semantic relevance)
  - **#9  Task 8**: Triager (dedup + rank + report + git_writer +
                    pr_comment, all dry-run by default)
  - **#10 Task 9**: Portal backend retheme (FastAPI `/api/tfactory/tasks`
                    + 5 artefact endpoints + WebSocket log stream)
  - **#11 Task 10**: Portal frontend retheme (React lane status grid,
                    task list, detail view, log viewer, shell)
  - **#12 Task 11**: End-to-end smoke (9 verification scenarios + bash
                    orchestrator + structural test harness +
                    operator guide)
  - **#13 Task 12**: Documentation + tag v0.1.0-mvp (this release)

### Pipeline architecture

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
   (#6)        (#7)              (#5)        (#8)        (#9)
```

Each agent's success path schedules the next via env-gated async tasks
(`TFACTORY_AUTO_PLAN`/`GENERATE`/`EVALUATE`/`TRIAGE`, all default ON).
Gen-Functional → Planner replan loops when a guardrail rejects, capped
by `replan_count >= 2` → `status=stuck`.

### Test surface

  | Suite | Cases | Run cmd |
  |---|---:|---|
  | Backend non-SDK | 531 | `pytest -q tests/` |
  | Frontend (vitest + RTL) | 112 | `cd apps/frontend-web && vitest run` |
  | E2e smoke (manual) | 9 scenarios | `scripts/e2e-smoke.sh --all` |

Every Claude SDK + Docker call is mocked at a seam so CI runs in seconds
without API keys or daemons. The e2e smoke exercises the real stack and
is operator-driven.

### Workspace layout

Per-task state lives at `~/.tfactory/workspaces/<proj>/specs/<spec>/`:

  - `status.json` — live status / phase / counts
  - `test_plan.json` — Planner's lane-tagged subtasks
  - `context/` — frozen AIFactory spec + diff + replan_request.json
  - `tests/` — Gen-Functional's generated pytest files
  - `findings/` — verdicts.json + triage_report.{md,json} + mutants/
  - `logs/` — per-agent transcripts

### Triager dry-run defaults

Per CLAUDE.md "no automatic pushes" policy, the Triager's git_writer
and pr_comment helpers default to dry-run (record the argvs they
WOULD invoke without executing). Operators opt in via env:

  - `TFACTORY_TRIAGER_GIT_WRITE=1` — actually commit accepted tests
  - `TFACTORY_TRIAGER_PR_COMMENT=1` — actually `gh pr comment`

### Deferred for v0.1.1+ (see #14)

  - **AIFactory `runners/github/` trim** (sub-task 8.4): ~21,600 LOC
    inherited PR-review machinery. Web-server still consumes parts;
    needs careful surgery.
  - **AIFactory spec-creation routes trim** (sub-task 9.2): inherited
    FastAPI endpoints we don't use.
  - **Inherited React spec wizard UI trim** (sub-task 10.2): 355
    TS/TSX files; TFactory components live alongside cleanly.
  - **Live log streaming**: WS sends ONE snapshot on connect at MVP;
    live tail-as-file-grows is Phase 2.
  - **Per-test coverage XML wiring**: `coverage_delta` primitive is
    fully tested but inputs aren't wired yet — degrades to
    "not computed".
  - **`source.json` PR + repo_slug fields**: snapshotter doesn't
    populate yet — operator sets them manually for now.

### Known sharp edges

See `guides/e2e-smoke.md` "Phase-2 backlog" for the full catalogue.
Highlights:

  - Verification field schema drift (`planner.md` emits `"command"`;
    dataclass has `run`). Both shapes accepted via duck-typing.
  - Manual smokes 6/8/9 lack auto-assertions (intentional —
    require docker/gh state changes).
  - NixOS devShell sets `NODE_ENV=production`; must `unset NODE_ENV`
    before `npm install` in `apps/frontend-web/`.

### Documentation

  - `README.md` — front door + quickstart
  - `CLAUDE.md` — guidance for Claude Code sessions inside this repo
  - `guides/e2e-smoke.md` — 9-scenario operator walkthrough
  - `guides/handover-to-tfactory-skill.md` — companion-skill reference
  - `guides/HANDOVER_WORKFLOW.md` — operator handover flow
  - `guides/CLAUDE_CODE_MCP_TOOLS.md` — MCP tool reference

---

## Unreleased

### ⚖️ Licensing

- **Relicensed from AGPL-3.0 → dual MIT OR GPL-3.0.** TFactory is now
  available under the recipient's choice of either license. See
  `LICENSE`, `LICENSE-MIT`, and `LICENSE-GPL`. SPDX identifier:
  `MIT OR GPL-3.0-only`. The `dataseek.team` enterprise-licensing
  contact line (which referenced a non-existent email) was removed.

### 🏷️ Branding

- **Rebrand `dataseeek` → `olafkfreund`.** The `dataseeek` GitHub org
  doesn't exist; every reference in non-archive files was rewritten to
  point at the actual repo location (`olafkfreund/TFactory`) and the
  actual GitHub Pages URL (`olafkfreund.github.io/TFactory`). Affects
  README badges, docusaurus config, package.json URLs, demo repo path,
  cosign verify identity in image-mirroring drills, and ghcr.io image
  paths in the Helm chart docs.

### 📚 Documentation

- **Full docs rewrite + GitHub Pages site.** The `guides/` directory was
  archived to `docs-archive/2026-05-26/guides/` (git history preserved).
  A fresh Docusaurus site at `docs/` is published to
  <https://olafkfreund.github.io/TFactory/> via a new
  `.github/workflows/docs.yml` workflow. Includes 18 reorganized pages:
  Getting Started, Demo, Concepts (3), Architecture (3 with Mermaid
  diagrams), Wiki (FAQ/Troubleshooting/Glossary), Showcase, Compliance
  (SOC2/GDPR), Contributing, Roadmap. The legacy `guides/` content is
  unchanged in archive form and still searchable via `git log --follow`.

- **README.md slimmed from 557 to 115 lines.** Hero + tagline + 60-second
  quickstart + demo callout + screenshot grid + prominent docs links.
  Everything operational moved to the docs site.

### ✨ Added

- **`scripts/demo.sh`** — end-to-end demo runner (Bash + jq + gh).
  Seeds `olafkfreund/tfactory-demo` with 3 issues, registers the repo
  with your portal, imports the issues as backlog tasks, prompts you
  to drive Claude Code from the terminal, then kicks off an autonomous
  build. Flags: `--yolo`, `--no-reset`, `--portal=URL`.

- **`scripts/capture-screenshots.ts`** — Playwright headless Chromium
  driver that captures 14 named PNGs of the marquee portal views to
  `docs/static/img/screenshots/`. Reproducible — anyone can refresh
  the gallery with `npm -w apps/frontend-web run capture-screenshots`.

- **`Justfile`** — canonical command index. `just --list` shows
  `install`, `backend`, `frontend`, `docs-dev`, `demo`, `screenshots`,
  `test-backend`, `test-frontend`, `test-postgres`, `test-all`.

- **Root `package.json` scripts**: `docs:install`, `docs:dev`,
  `docs:build`, `demo`, `screenshots`.

---

## 3.0.2 - 2026-05-26

Patch release fixing two leftover wiring + branding bugs from v3.0.0.

### 🛠️ Fixed

- **P6 observability never wired into `main.py`**. The
  `server/observability/` package shipped in v3.0.0 (Epic #26 P6)
  but `main.create_app()` never called `install_metrics(app)`,
  `configure_structlog()`, or `app.add_middleware(CorrelationIdMiddleware)`.
  As a result the production portal exposed neither `/metrics` nor
  structured JSON logs nor correlation IDs — despite all P6 unit
  tests passing (they built their own minimal FastAPI app and called
  the functions directly, bypassing main.py). v3.0.2 wires the three
  calls in the correct order:
  - `configure_structlog()` at the top of `create_app()` so
    boot-time logs are already JSON.
  - `CorrelationIdMiddleware` added LAST so it's the outermost
    layer (sets X-Request-ID before TokenAuth runs; 401 responses
    still carry the ID — auditors rely on this).
  - `install_metrics(app)` after all routers are mounted so the
    Prometheus instrumentator can derive cardinality-capped
    `handler` labels from the route table.

  Regression test added at `tests/obs/test_p6_main_wiring.py`:
  imports `main.create_app()` and asserts `/metrics` returns 200 +
  CorrelationIdMiddleware echoes back `X-Request-ID` + the FastAPI
  app title is TFactory + `app.version` matches the package version.
  Gates every PR forward.

- **Leftover Magestic branding in `main.py`**. The v3.0.0 rebrand
  missed three string constants:
  - `title="Magestic AI Web API"` → `"TFactory Web API"`
  - `description="Web API for Magestic AI autonomous coding framework"`
    → `"Web API for TFactory — self-hosted AI task management +
    agent orchestration"`
  - Root-route message `"Magestic AI Web Server"` →
    `"TFactory Web Server"`

  Plus the hardcoded `version="1.0.0"` on the FastAPI app + on
  `/api/health` was a drift hazard. v3.0.2 reads the canonical
  version from `apps/backend/__init__.py` at startup (the file
  `bump-version.js` already updates on every release), via a tiny
  `_read_app_version()` helper. No more silent version-skew.

### Upgrade notes

- Backwards-compatible patch: `helm upgrade tfactory --version 3.0.2`
  picks up both fixes with no schema or config changes.
- Operators who deployed v3.0.1 had a non-functional `/metrics`
  endpoint. After upgrading, configure your Prometheus scrape job
  against the now-live endpoint (see `docs-archive/2026-05-26/guides/operations/observability.md`).

## 3.0.1 - 2026-05-26

Patch release with two operator-visible fixes.

### 🛠️ Fixed

- **SQLite migration crash on fresh install**. The P2.3
  `encrypt_credentials` migration (`c6e3b2d4a8f0`) used a direct
  `op.alter_column(nullable=False)` to re-apply the NOT NULL
  constraint on `email_accounts.access_token` after the encrypted-
  column swap. SQLite doesn't support `ALTER TABLE ... ALTER
  COLUMN ... SET NOT NULL` — backends booting against a fresh
  SQLite (`autoApply=true` in the Helm chart's POC path; default
  local-dev path) crashed during `alembic upgrade head`. Wrapped
  the step in `op.batch_alter_table`, mirroring P3.3's
  `d8f1a3c5e7b9` migration. Postgres deployments are unaffected
  (their behavior was correct via the same native ALTER).
  Regression test added at `tests/secrets/test_p2_sqlite_migration.py`
  that runs `alembic upgrade head` against a temp SQLite file —
  gates every PR going forward.

- **TFactory logo not displaying in the sidebar/loading screen/
  onboarding**. The new logo + favicon assets were stashed before
  P1 work began and never restored to the main release. Bundle
  contains the updated `logo.png` (547 KB, full-res TFactory
  brand), `favicon.ico` (15 KB), `apple-touch-icon.png` (43 KB),
  and 16/32 px favicon variants. The sidebar `<img src="/logo.png">`
  reference is unchanged — the new files just slot in.

### Upgrade notes

- **Operators on v3.0.0**: this is a backwards-compatible patch.
  `helm upgrade` to v3.0.1 picks up both fixes.
- **Operators who already migrated** (the SQLite migration crash
  blocked them from getting that far on v3.0.0): no special
  handling needed — fresh install + `helm install tfactory --version 3.0.1`
  works end-to-end.

## 3.0.0 - 2026-05-26

The TFactory **enterprise GA** release (Epic #26). Self-hosted Helm
chart with PSS-restricted defaults, encrypted-at-rest secrets backed
by 5 KMS backends, OIDC SSO, tamper-evident audit chain, GDPR
right-to-erasure, structured-JSON observability + Prometheus
metrics, and a full SOC 2 / GDPR / STRIDE evidence pack with three
ship-readiness drill scripts.

### ⚠ Breaking changes

- **Forward-only schema migration** `c6e3b2d4a8f0_encrypt_credentials`:
  `email_accounts.access_token`, `email_accounts.refresh_token`, and
  `llm_endpoints.api_key` columns convert from plaintext `Text` to
  encrypted `LargeBinary`. The migration is **forward-only** — there
  is no downgrade path. Operators MUST take a `pg_dump` backup before
  upgrading from any v2.x install.
- **Required Postgres backend for production**: SQLite remains
  supported for dev/POC, but `kms_data_keys` + the audit chain
  expect Postgres semantics for indexed lookups.
- **Container runs as non-root uid 65532** with read-only root
  filesystem and dropped capabilities. Operators with custom
  init-containers writing to `/` must mount tmpfs/emptyDir.

### ✨ Added — Epic #26 phases

- **P0 — Container hygiene**: Chainguard distroless base
  (digest-pinned), Trivy CVE scan, Syft SBOM, cosign keyless
  signing via GitHub OIDC, multi-arch (amd64+arm64) manifest
  inspection.
- **P1 — Postgres backend**: `asyncpg` driver, Alembic migrations,
  optional `APP_MIGRATIONS_AUTO_APPLY=false` for Helm Job mode,
  bank-grade privilege model (no SUPERUSER, no CREATE EXTENSION).
- **P2 — Encrypted secrets at rest**: `EncryptedString`
  `TypeDecorator` over `LargeBinary`, per-org `kms_data_keys` with
  LRU cache, 5 KMS backends (`fernet` for dev, `aws_kms`,
  `vault_transit`, `azure_kv`, `gcp_kms`), root-key rotation CLI
  (`python -m server.crypto rotate-root`), forward-only column
  migration with KMS-aware backfill.
- **P3 — OIDC SSO**: `authlib`-based Authorization Code + PKCE +
  state + nonce, JIT user/`OrganizationMember` provisioning with
  claim-mapped roles (`APP_OIDC_GROUP_TO_ROLE`), 15-minute access
  TTL + 8-hour refresh, IdP-validated refresh path with userinfo
  caching, logout redirect to IdP `end_session_endpoint`. Presets
  for Keycloak, Okta, Azure AD.
- **P4 — Helm chart**: `charts/tfactory/` with PSS-restricted
  security contexts, default-deny NetworkPolicy + 443 egress
  allowlist, ExternalSecret templates for 4 backends, optional
  bundled Postgres `StatefulSet` for POC mode, `customCABundle`
  for TLS-intercepting corporate proxies, schema-validated
  `values.yaml`.
- **P5 — Audit hardening**: SHA-256 hash chain on every audit-log
  write, NDJSON + CSV streaming export at `/api/audit/export`,
  air-gappable external verifier (`python -m server.audit
  verify-chain`), GDPR Art. 17 erasure that re-hashes the chain so
  `verify-chain` continues to pass, daily retention job (default
  13 months = SOC 2 12 + buffer).
- **P6 — Observability**: `structlog` JSON-to-stdout with
  ISO-8601 timestamps + `request_id` binding, correlation-ID
  middleware (`X-Request-ID`) with `httpx` propagation, Prometheus
  `/metrics` with cardinality-capped `handler` labels (route
  templates, not raw paths), optional `METRICS_SCRAPE_TOKEN`
  bearer gate, Helm `ServiceMonitor` template, pre-built Grafana
  dashboard JSON (7 panels).
- **P7 — Evidence + ship-readiness drills**: SOC 2 evidence pack
  (CC1-CC9 + A1 + C1), GDPR DPIA + data-flow diagram, STRIDE
  threat model, 4-cloud-path deployment runbook (EKS+RDS / AKS+
  Azure Postgres / GKE+Cloud SQL / vanilla K8s+Vault), v0.x → v3.0
  upgrade guide, three executable drill scripts
  (`backup-restore.sh`, `upgrade-in-place.sh`, `image-mirroring.sh`)
  with `--dry-run` modes.

### 📚 Documentation

New operator runbooks under `guides/`:
- `guides/operations/audit-trail.md`
- `guides/operations/encrypted-secrets-dr.md`
- `guides/operations/image-mirroring.md`
- `guides/operations/kms-rotation-runbook.md`
- `guides/operations/observability.md`
- `guides/operations/oidc-setup.md`
- `guides/deployment/helm-install.md`
- `guides/deployment/runbook.md`
- `guides/deployment/upgrade.md`
- `guides/compliance/soc2-evidence.md`
- `guides/compliance/dpia-data-flow.md`
- `guides/security/threat-model.md`
- `guides/observability/grafana-tfactory.json`

### 🧪 CI

11 acceptance jobs gate every PR (≈2000 tests total):
`backend (ruff + pytest)`, `docker (P0)`, `postgres (P1) × {15, 16}`,
`secrets (P2)`, `oidc (P3)`, `helm (P4)`, `audit (P5)`, `obs (P6)`,
`evidence (P7)`, `frontend (typecheck)`.

### ⚠ Documented v3.0 limitations (v3.1 follow-ups)

Tracked in `guides/compliance/soc2-evidence.md § Documented
limitations`. Each maps to a v3.1 Epic #35 issue:

1. Audit chain has no signed external anchor.
2. Revocation latency bounded by 15-minute access-token TTL (back-
   channel logout deferred).
3. FIPS 140-2/3 modules not validated.
4. No built-in OpenTelemetry distributed tracing.
5. Single-replica only (multi-replica via Redis pub/sub deferred).
6. LLM-call audit deferred to v3.1 LiteLLM gateway.

### ✨ Added
- **GitHub PR Review Integration**: End-to-end support for PR reviews including listing, fetching, posting reviews, checking new commits, and viewing logs via dedicated API endpoints.
- **PR Review WebSocket Events**: Real-time progress, completion, and error events via WebSocket for live feedback during PR reviews.
- **PR Action Endpoints**: Support for posting reviews, commenting, merging, assigning, and canceling PRs through backend API.
- **AI-Powered Conflict Resolution**: Enhanced "Fix Conflicts with AI" functionality with real git merge and AI resolution of conflict markers.
- **Task from Chat Feature**: Button in Insights chat to convert conversation into a structured task (title + PRD description) with editable preview.
- **Open in Browser**: New "Open in Browser" button in EditorPage that serves files with correct MIME types and asset URL rewriting.
- **QA Fixer Phase**: Added separate `qa_fixer` phase in phase configuration, allowing independent model and thinking settings.
- **Phase-Scaled Progress**: Monotonically increasing progress percentages across phases (planning 0–20%, coding 20–80%, QA 80–95%, complete 95–100%).
- **Terminal Persistence**: TerminalGrid now remains mounted across view switches to prevent stuck terminals and lost PTY connections.
- **Model & Token Metrics**: Display assistant model name on chat messages and show tokens/sec metrics after each response across all providers.
- **Dark Theme & UI Improvements**: Enhanced folder navigation, keyboard support (Enter/Backspace), HTML preview, progress labels, and overall dark theme consistency.

### 🛠️ Fixed
- **GitHub PR Connection Detection**: Fixed incorrect endpoint call (`window.API.github.checkGitHubConnection` → `window.API.checkGitHubConnection`).
- **AI Merge Conflict Resolution**: Fixed syntax error in `github.py` caused by AI-generated extra closing brace.
- **requireReviewBeforeCoding Sync**: Ensured field is written to `task_metadata.json` when editing tasks.
- **Email Notifications**: Fixed silent failure under legacy token auth by populating default user context.
- **Build Progress & Subtask Status**: Added fallback in `post_session_processing` to detect new commits and force-update status.
- **File Serving 404s**: Resolved `404` errors for `/api/files/serve` by properly staging the endpoint and enabling public access with path-traversal protection.
- **Model Config Loss**: Fixed `UpdateModelConfigRequest` to preserve all fields (provider, profileId, model, thinkingLevel, temperature).
- **Issue-to-Task Creation**: Fixed backend `TaskMetadata` model to include `githubIssueNumber`, `affectedFiles`, and `acceptanceCriteria`.
- **Sidebar Layout**: Restored proper layout and spacing in sidebar components.

### 🔧 Changed
- **Project Renaming**: Renamed from "Claude Code Manager Web" to **TFactory** across UI, navigation, and documentation.
- **MCP Template Filtering**: Removed redundant and duplicate quick templates (filesystem, fetch, github, gitlab) that conflict with native tools.
- **Hardcoded Model Values**: Replaced inline model/thinking defaults with shared constants to ensure user-configured settings take effect.
- **Git Ignore Safety**: Added `.tfactory-security.json` and `.tfactory-status` to `.gitignore` during project init and unstage during merges.
- **CLI Detection Optimization**: Improved speed using `shutil.which` and `npm package.json` parsing instead of slow Node.js startup (~4s → <50ms).

### 📦 Updated
- **README.md**: Updated project documentation with fixed GitHub URL, removed non-existent files, and added Docker deployment guide.
- **Phase Progress Logic**: Refactored progress logic to prevent backward jumps between phases using defined phase ranges.