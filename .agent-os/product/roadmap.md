# Product Roadmap

> Last Updated: 2026-06-03
> Version: 1.0.0
> Status: Active

Derived from the 2026-05-30 PM review (internal audit + 15-competitor market
study) captured in epic [#33](https://github.com/olafkfreund/TFactory/issues/33).

## Phase 0: Already Shipped (v0.1.0-mvp → v0.5.0)

**v0.3.0 → v0.5.0 (2026-06-03):**

- [x] Cloud infrastructure testing — AWS · GCP · Azure read-only posture (epic #133)
- [x] Credential Broker — pluggable secrets backends, ephemeral creds, honest egress, WIF (#62/#73/#74)
- [x] Test-target login (storageState, #107), Kubernetes port-forward targets (#108, live-verified)
- [x] Visual Inspection Run — Playwright record + screenshots + correction plan (epic #170)
- [x] SaaS connector targets — ServiceNow / Salesforce / SAP / MuleSoft (#111)
- [x] **Bidirectional AIFactory ↔ TFactory loop** — hand-back + bounded re-test (epic #182, v0.5.0)
- [x] Flagship-grade portal redesign + Cloud Reports / Visual Reports pages

**Epic #232 — trustworthy, deployment-aware testing (2026-06-06):**

- [x] Numeric confidence score from the weighted 5-signal verdict (#238)
- [x] Flaky-history wired into the verdict + scoring regression corpus (#239)
- [x] Backstage TechInsights test-quality emitter (#240) + per-component badge (#241)
- [x] Pre-lane health-check gate + deployed-URL resolution (#234)
- [x] Security hardening — rate-limited/constant-time webhook, signed commits (#242)
- [x] Testing-model + security-hardening guides (#243) — `guides/testing-model.md`
- [ ] Build→deploy→test orchestration (#233) · Cypress/Vitest images (#236) · Java lane (#237)

**v0.1.0-mvp → v0.2.0:**

- [x] 4-agent pipeline end-to-end — Planner → Gen → Executor → Evaluator → Triager
- [x] 5-signal verdict (coverage · stability · mutation · flake-lint · semantic)
- [x] Unit lane: pytest (Python) **and** Jest (TypeScript)
- [x] Browser lane: Playwright (TypeScript) via `AppRuntime`
- [x] API + Integration lanes (v0.2)
- [x] Framework descriptor registry + `.tfactory.yml` + tests-catalog
- [x] Test evidence capture (screenshots / video / trace / HAR) + portal Evidence tab
- [x] MCP server + `/handover-to-tfactory`, `/tfactory-init`, `/tfactory-add-test`
- [x] Dry-run-by-default git commit + PR comment
- [x] BYO-LLM provider factory (Claude / Codex / Gemini / Ollama / OpenAI-compatible)

## Horizon 1 — Now: credibility blockers

- [x] Reconcile docs ↔ code lane vocabulary (#34 — README/CLAUDE done; docs/ snapshots pending)
- [x] Fix the version stamp 3.0.2 → 0.2.1 (#35)
- [x] Green the CI baseline (#46)
- [ ] This product documentation set (#36)

## Horizon 2 — Next: table-stakes gaps

- [ ] Flaky-test history — cross-run flip-rate signal (#37) `M`
- [ ] BYO-LLM / air-gapped as a marketed, verified feature (#38) `S`

## Horizon 3 — Later: moat & strategy

- [x] Decouple from AIFactory — ingest generic AC sources (Gherkin/EARS/markdown) (#40) `L`
- [ ] Mutation-driven generation across languages (Stryker/PIT) (#41) `L`
- [x] Define freemium GTM + pricing (#42) `M` — see `pricing.md`
- [~] Trim inherited AIFactory dead weight (#43) `L` — tranche 1 done; runners/github cut pending a dedicated refactor

## Effort Scale

- `XS`: 1 day · `S`: 2-3 days · `M`: 1 week · `L`: 2 weeks · `XL`: 3+ weeks
