# Changelog

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

### Tasks shipped so far in v0.2

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

### In progress

- **#17 Task 1**: Framework descriptor registry
- **#18 Task 2**: `.tfactory.yml` schema + parser + validator
- **#19 Task 3**: `.tfactory/tests-catalog.json` schema + helpers
- **#20-#32 Tasks 4-16**: see `docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md`

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