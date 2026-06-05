---
layout: default
title: Progress
permalink: /progress/
nav_order: 8
---

# Progress

> Live delivery snapshot, updated by hand as merges land — last refresh
> **2026-06-05**. Older milestones kept below as the historical record. Full
> detail per release in the
> [changelog](https://github.com/olafkfreund/TFactory/blob/main/CHANGELOG.md).

## At-a-glance — current

```
Unreleased — Factory PARR spine: governed pickup + RFC-0001 + Backstage
  ████████████████████████████  #193–#197 · #198 #211 #214 #224 · #215 #216 #223

v0.5.0 — Bidirectional AIFactory ↔ TFactory integration   (2026-06-03)
  ████████████████████████████  epic #182 — 7 phases shipped + #108 live-fix

v0.4.0 — Visual Inspection Run + SaaS connector targets    (2026-06-03)
  ████████████████████████████  epic #170 (P1/P2/P4/P5) · #111 · #107

v0.3.0 — Cloud testing (AWS/GCP/Azure) + platform + portal (2026-06-03)
  ████████████████████████████  epic #133 · #74 #73 #71 #85 #108 #109 #107 #138

v0.2.0 — Enterprise Test Framework Spine                   (2026-05-29)
  ████████████████████████████  16 of 16 tasks (#16–#32) + evidence-links

Releases:  https://github.com/olafkfreund/TFactory/releases
Backend tests: 531 (v0.1) → 1225 (v0.2) → 2803 (v0.5)
```

## Unreleased — Factory PARR spine

TFactory becomes a **verified node in the
[Factory](https://factory.freundcloud.com/) line** rather than a standalone tool:

- **Governed pickup from PFactory (#193–#197)** — recognise + enqueue governed
  test targets, parse `pfactory:meta` as the test oracle, then generate · run ·
  report back up the spine.
- **RFC-0001 normalized completion event (#198 / #211 / #214 / #224)** — the
  Triager emits one cross-service envelope with a shared `correlation_key`;
  CFactory watches a single contract across AIFactory · PFactory · TFactory.
  Default port moved 3102 → 3103. See the
  [completion-event envelope]({{ '/completion-event-envelope/' | relative_url }}).
- **Backstage onboarding (#215 / #216 / #223)** — `catalog-info.yaml` + TechDocs,
  enriched annotations, an AI-assistant skill descriptor.
- **Reach more SUTs** — multi-step / SSO login (#107), `toHaveScreenshot`
  baselines wired to the portal store (#109), live K8s port-forward fix (#108).

## v0.5.0 — Bidirectional AIFactory ↔ TFactory integration (2026-06-03)

The reverse of the handover: when TFactory's tests find problems, it hands a
**correction** back to AIFactory's QA Fixer, then re-tests — bounded so it can't
run away. Epic [#182](https://github.com/olafkfreund/TFactory/issues/182),
seven phases shipped.

| Phase | What | Issue |
|---|---|---|
| P1 | `source.json` records the AIFactory hand-back target | [#183](https://github.com/olafkfreund/TFactory/issues/183) |
| P2 | `agents/handback/` correction builder + renderer (pure) | [#184](https://github.com/olafkfreund/TFactory/issues/184) |
| P3 | AIFactory receiver: `apply-correction` REST + MCP + QA Fixer | [AIFactory#317](https://github.com/olafkfreund/AIFactory/issues/317) |
| P4 | dry-run-first sender + Triager completion hook | [#185](https://github.com/olafkfreund/TFactory/issues/185) |
| P5 | `/handback-to-aifactory` skill + companion + CLI | [#186](https://github.com/olafkfreund/TFactory/issues/186) |
| P6 | bounded closed loop + `/tfactory-fixloop` | [#187](https://github.com/olafkfreund/TFactory/issues/187) |
| Docs | `guides/aifactory-handback.md` + round-trip updates | [#188](https://github.com/olafkfreund/TFactory/issues/188) |

Also this cycle: **[#108](https://github.com/olafkfreund/TFactory/issues/108)** —
the Kubernetes port-forward dispatch was **live-verified** against a real kind
cluster, fixing two defects the mocked tests masked (a missing `port_forward`
attribute; an IPv4-only readiness parse that hung on kubectl's `[::1]` line).
Design: [`docs/plans/2026-06-03-aifactory-tfactory-handback-design.md`](https://github.com/olafkfreund/TFactory/blob/main/docs/plans/2026-06-03-aifactory-tfactory-handback-design.md).

## v0.4.0 — Visual Inspection Run + SaaS connectors (2026-06-03)

- **Visual Inspection Run (epic [#170](https://github.com/olafkfreund/TFactory/issues/170)).**
  Record a Playwright browser run — trace + video + step-labelled *verification
  and error* screenshots — and package a human report, an LLM correction plan,
  and a GitHub-issue export into `automated-test/<datetime>/`, surfaced in the
  portal's **Visual Reports**. P1/P2/P4/P5 shipped; P3 (ServiceNow SSO) needs a
  live tenant.
- **SaaS connector target ([#111](https://github.com/olafkfreund/TFactory/issues/111)).**
  A first-class `type: connector` target (ServiceNow / Salesforce / SAP /
  MuleSoft) over the http + credential-vault auth.
- **storageState login-once ([#107](https://github.com/olafkfreund/TFactory/issues/107)).**
  Gen-Functional scaffolds `auth.setup.ts` so a browser test logs in once and
  reuses the session.

## v0.3.0 — Cloud testing + platform foundations + portal redesign (2026-06-03)

- **Cloud infrastructure testing — AWS · GCP · Azure (epic
  [#133](https://github.com/olafkfreund/TFactory/issues/133), complete).**
  Read-only posture: access gate → discovery → Mermaid topology → Prowler/CIS
  (OCSF) → accept/flag/reject → remediation plan. All three live-verified.
  Launch from **+Task → Cloud Infrastructure**; reports land in **Cloud Reports**.
- **Credential Broker (epic [#62](https://github.com/olafkfreund/TFactory/issues/62)).**
  Pluggable secrets backends, ephemeral 0600 file creds, honest egress gate,
  sandbox injection (#73), OIDC/workload-identity federation (#74).
- **k8s dispatch (#108)**, **test-target login (#107)**, **visual-regression
  baselines (#109)**, **Triager completion callback (#85)**, and a flagship-grade
  **portal redesign**.

## v0.2.0 — task-by-task (historical record)

> The 16-task enterprise spine that v0.3+ builds on. Kept for provenance.

| # | Task | Merge | Tests added |
|---|---|---|---|
| [#16](https://github.com/olafkfreund/TFactory/issues/16) | Task 0: Lane spine rename (functional / sast / dast / fuzz / mutation → unit / browser / api / integration / mutation) | `1ae97f9` + 4 prior commits | +25 alias coverage |
| [#17](https://github.com/olafkfreund/TFactory/issues/17) | Task 1: Framework descriptor registry (pytest + Jest + Playwright at MVP) | [`1c92280`](https://github.com/olafkfreund/TFactory/commit/1c92280) (7 commits) | +45 |
| [#18](https://github.com/olafkfreund/TFactory/issues/18) | Task 2: `.tfactory.yml` schema + parser + validator (http / k8s / docker_compose / feature_flag targets) | [`6359590`](https://github.com/olafkfreund/TFactory/commit/6359590) | +69 |
| [#19](https://github.com/olafkfreund/TFactory/issues/19) | Task 3: `.tfactory/tests-catalog.json` schema + 3-step AC-match lookup | [`51fcebd`](https://github.com/olafkfreund/TFactory/commit/51fcebd) (6 commits) | +50 |
| [#20](https://github.com/olafkfreund/TFactory/issues/20) | Task 4: Snapshotter extension — lifts `.tfactory.yml` + tests-catalog into workspace context | [`7b2fc8b`](https://github.com/olafkfreund/TFactory/commit/7b2fc8b) (4 commits) | +9 |
| [#21](https://github.com/olafkfreund/TFactory/issues/21) | Task 5: Planner per-subtask polyglot (language / framework / target_name / intent) | [`43e9ac1`](https://github.com/olafkfreund/TFactory/commit/43e9ac1) (6 commits) | +52 |
| [#22](https://github.com/olafkfreund/TFactory/issues/22) | Task 6: Gen-Functional generic — descriptor.context_block injection; v0.1 prompt preserved as legacy | [`4c006fb`](https://github.com/olafkfreund/TFactory/commit/4c006fb) (6 commits) | +25 |
| [#23](https://github.com/olafkfreund/TFactory/issues/23) | Task 7: Per-framework Docker runner images (pytest / jest / playwright) | [`f3f88dd`](https://github.com/olafkfreund/TFactory/commit/f3f88dd) (5 commits) | +6 (skipped when no daemon) |
| [#24](https://github.com/olafkfreund/TFactory/issues/24) | Task 8: Browser-lane AppRuntime — docker-compose lifecycle + HTTP health-poll | [`d2aa2ae`](https://github.com/olafkfreund/TFactory/commit/d2aa2ae) (6 commits) | +25 |
| [#25](https://github.com/olafkfreund/TFactory/issues/25) | Task 9: TypeScript Evaluator primitives (tsc / ESLint / Stryker) | [`fde90ef`](https://github.com/olafkfreund/TFactory/commit/fde90ef) (6 commits) | +100 |
| [#26](https://github.com/olafkfreund/TFactory/issues/26) | Task 10: Evaluator coverage adapter — null-not-zero for Browser lane (Decision 11) | [`4ef9a51`](https://github.com/olafkfreund/TFactory/commit/4ef9a51) (4 commits) | +21 |
| [#28](https://github.com/olafkfreund/TFactory/issues/28) | Task 12: 15 starter test templates (5 each for Playwright / Jest / pytest) | [`e58adfd`](https://github.com/olafkfreund/TFactory/commit/e58adfd) (5 commits) | +48 |
| [#29](https://github.com/olafkfreund/TFactory/issues/29) | Task 13: Skills + slash commands (`/tfactory-init` · `/tfactory-add-test` · `/tfactory-from-template` + handover update) | [`782259f`](https://github.com/olafkfreund/TFactory/commit/782259f) (5 commits) | +36 |
| [#30](https://github.com/olafkfreund/TFactory/issues/30) | Task 14: Portal endpoints — frameworks / templates / skills / catalog | [`94e711b`](https://github.com/olafkfreund/TFactory/commit/94e711b) (6 commits) | +35 effective (43 declared; 8 hit the pre-existing starlette shim path) |
| [#27](https://github.com/olafkfreund/TFactory/issues/27) | Task 11: Triager update-vs-create + catalog mutation (3-step `lookup_by_ac` decides UPDATE-in-place / CREATE-new / SKIP-locked) | [`cd5396b`](https://github.com/olafkfreund/TFactory/commit/cd5396b) (5 commits) | +24 |
| [#31](https://github.com/olafkfreund/TFactory/issues/31) | Task 15: `LaneStatusGrid` full reskin (5 independently lit lanes) + `tfactory init` / `tfactory migrate v0_1_catalog` CLIs | [`f4eb9aa`](https://github.com/olafkfreund/TFactory/commit/f4eb9aa) (5 commits) | +21 backend + 27 frontend |
| [#32](https://github.com/olafkfreund/TFactory/issues/32) | Task 16: Test evidence capture (screenshots / video / trace / HAR) + retention enforcer + portal endpoint + frontend Evidence tab | [`654e77a`](https://github.com/olafkfreund/TFactory/commit/654e77a) (5 commits; commit 4 deferred for follow-up PR) | +86 |

## v0.2.0 — released 2026-05-29

All 16 v0.2 tasks merged to `main` and the Task 16 deferred commit 4
follow-up (Triager PR-comment evidence-links, `5d8f588`) landed
immediately afterwards. Annotated tag `v0.2.0` pushed; GitHub Release
live at
<https://github.com/olafkfreund/TFactory/releases/tag/v0.2.0>.
Release body mirrors the v0.2.0 CHANGELOG section.

The Triager follow-up was the only piece held back during the parallel
batch (it block-waited on Task 11). It now ships portal evidence links
per accepted/flagged candidate in `triage_report.md` so PR reviewers
click straight from the comment to screenshots / video / trace.zip /
network.har.

## Test totals

| Snapshot | Backend tests | Δ vs prior | Notes |
|---|---:|---:|---|
| v0.1.0-mvp baseline (2026-05-28) | **531** | — | Walking skeleton — Python + pytest only |
| v0.2 in progress (post-batch-1, 2026-05-29) | **1039** (7 skipped) | **+508** | After 14 of 16 v0.2 tasks |
| v0.2 release candidate (post-batch-2, 2026-05-29) | **1170** (7 skipped) | **+131** | After all 16 v0.2 tasks — exceeded the +~110 forecast |
| v0.2.0 released (`5d8f588`, 2026-05-29) | **1177** (7 skipped) | **+7** | After Triager evidence-links follow-up |
| v0.2 post-release (HEAD `bcc5c7d`, 2026-05-29) | **1225** (7 skipped) | **+48** | Starlette `.content`→`.body` shim sweep + showcase + iframe demo |
| v0.3.0 (Cloud + Credential Broker + portal, 2026-06-03) | ~2200 | **+~975** | Cloud assessment, secrets backends, WIF, sandbox injection, portal redesign |
| v0.4.0 (Visual Inspection + connectors, 2026-06-03) | ~2400 | **+~200** | `agents/visual_inspection/`, connector registry, storageState scaffold |
| **v0.5.0 (Hand-back loop + #108, 2026-06-03)** | **2803** | **+~400** | `agents/handback/` (request/render/send/trigger/loop), k8s live-fix |

The docker-runner smoke tests (#23) require a live daemon and gracefully skip
when none is present.

## Pipeline status

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
  (polyglot   (generic via      (DockerRunner    (5 signals;     (catalog-aware
   per #21)    descriptor #22)   + AppRuntime     null coverage;  in #27 →
                                  for Browser     TS primitives   evidence-links
                                  #24)            #25)            from 5d8f588)
```

All 16 v0.2 tasks shipped + the deferred Triager evidence-links
follow-up landed. The pipeline is now end-to-end demonstrable —
see the [live v0.2.0 showcase]({{ '/showcase/' | relative_url }})
running against [olafkfreund/tfactory-demo](https://github.com/olafkfreund/tfactory-demo).

## v0.2.0 demo + showcase

A live end-to-end run published at [`/showcase/`]({{ '/showcase/' | relative_url }}):

- **System under test:** [olafkfreund.github.io/tfactory-demo/](https://olafkfreund.github.io/tfactory-demo/) (Vite + React)
- **5 Playwright tests** generated from the 5-AC user story; **4 pass, 1 fails** (AC#5 surfaces a seeded cache bug)
- **Evidence captured:** 5 screenshots + AC#5 video.webm + AC#5 trace.zip
- **Planner output:** real [test_plan.json](https://raw.githubusercontent.com/olafkfreund/tfactory-demo/main/showcase-evidence/test_plan.json) emitted via the Claude subscription (no API keys)
- **Tag:** [`v0.2.0-demo`](https://github.com/olafkfreund/TFactory/releases/tag/v0.2.0-demo) anchors the showcase commit (`be52f9b`)

## Health signals

- ✅ `scripts/verify-fork.sh --no-import` — passes against the v0.2 module set
- ✅ pytest (backend) — **2803 tests** at v0.5.0 (1225 at v0.2.0)
- ✅ All 5 portal endpoints respond shim-compatibly without fastapi installed
- ✅ Pages site builds + deploys on every push to `main` (Cayman + custom layout)
- ✅ Showcase page renders the live SUT in an iframe + inline triage report with working evidence links
- ⚪ Docker integration — runner images build under CI; per-image smoke
  tests skip locally without a daemon (recovers via `tfactory-test`)
- ⚪ End-to-end smoke (`scripts/e2e-smoke.sh`) — 9 scenarios, operator-driven

## Cross-references

- Live demo + showcase → [Demo]({{ '/showcase/' | relative_url }})
- v0.2 driver doc → [`docs/plans/2026-05-28-enterprise-test-frameworks-design.md`](https://github.com/olafkfreund/TFactory/blob/main/docs/plans/2026-05-28-enterprise-test-frameworks-design.md)
- v0.2 task plan → [`docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md`](https://github.com/olafkfreund/TFactory/blob/main/docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md)
- Architecture → [Architecture]({{ '/architecture/' | relative_url }})
- Changelog → [CHANGELOG.md](https://github.com/olafkfreund/TFactory/blob/main/CHANGELOG.md)
- v0.2.0 release → [github.com/olafkfreund/TFactory/releases/tag/v0.2.0](https://github.com/olafkfreund/TFactory/releases/tag/v0.2.0)
- v0.2.0-demo tag → [github.com/olafkfreund/TFactory/releases/tag/v0.2.0-demo](https://github.com/olafkfreund/TFactory/releases/tag/v0.2.0-demo)
- Epic + sub-issues → [github.com/olafkfreund/TFactory/issues](https://github.com/olafkfreund/TFactory/issues)
