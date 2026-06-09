# TFactory — Enterprise 90-Day Plan

> Created: 2026-06-09
> Horizon: ~90 days (≈13 weeks)
> Capacity: Solo + Claude Code
> Status: Proposed (decisions locked with the owner; see "Decisions" below)

## Thesis (the 0.1%-practitioner read)

TFactory's **moat is the verdict pipeline, not test generation.** Anyone can
prompt an LLM to write tests; almost nobody *proves a generated test is worth
keeping* — TFactory already does, with seven real signals (coverage-delta · 3×
stability · mutation-kill · flake-lint promotion · LLM semantic relevance ·
CI-parity #302 · flaky-history #37). That is defensible and it is **real in the
code today** for Python and TypeScript.

To become an enterprise go-to tool we therefore do **not** need a cleverer
engine. We need: **(a) a frictionless front door** (make "Test" a *gate* in the
workflow people already use — a PR status check — plus a no-AIFactory ingestion
path), **(b) the multi-tenant/observability/compliance table-stakes** procurement
demands, and **(c) breadth** (more languages, more SCMs, brownfield) — most of
which is deliberately deferred past this 90-day window.

## Decisions (locked with owner, 2026-06-09)

| Dimension | Decision |
|---|---|
| Deployment | Both SaaS + on-prem — **build self-host-first, but tenant-clean** |
| Primary wedge | **AIFactory companion QA** (deepen the per-feature loop) |
| Languages | Py + TS today; **Java → C# → Go** as expansion (only **Java** this phase) |
| Scale | Modest, per-customer self-hosted (no distributed queue this phase) |
| Compliance | SOC 2 + ISO 27001 + GDPR (finish the currently-xfail evidence) |
| SCM | GitHub + GitLab + Bitbucket + Azure DevOps — **GitHub-only this phase** |
| Review step | **PR-native**: triage report + pass/fail status check |
| Generic ingestion | **Yes** — first-class `.feature`/EARS/markdown front door |
| Brownfield uplift | On the roadmap, **not this phase** |
| Standalone "new test" | Portal "New test" action + existing MCP/`add-test`; **IDE deferred** |

## Current state (ground truth, from the 2026-06-09 code audit)

**Real & end-to-end:** Python (pytest) + TypeScript (Jest) generation; Playwright
browser lane (AppRuntime lifecycle); 5-lane dispatch (`lane_dispatch.py`); Docker
sandbox `--network=none --read-only` (`docker_runner.py`); all 7 verdict signals
for Python; AIFactory handover (snapshot + diff + Task Contract v2); bidirectional
fix-loop; OIDC+JWT+`acw_` keys/scopes; org/RBAC model; hash-chained audit; KMS-
encrypted secrets; cosign+SBOM release; BYO-LLM egress classifier.

**Gaps that matter (this plan attacks the starred ones):**
1. ★ No **PR status check** — Triager posts a comment, but nothing gates merge.
2. ★ **Generic ingestion is CLI-only** — `spec_sources.py` parses md/Gherkin/EARS but has no MCP tool or portal door.
3. ★ **`projects` route uses JSON files, not the DB** — not org-scoped; a real multi-tenant hole that would force a rewrite later.
4. ★ **Java is a wedge** — descriptor + mutation-regex + JaCoCo parser exist; no generation wiring, no PIT runner integration.
5. ★ **No metrics/tracing** (no Prometheus/OTel); **compliance evidence is `xfail`** (#160).
6. Polyglot beyond Py/TS (C#/Go), multi-SCM (GitLab/BB/ADO), brownfield, SaaS-scale queue/workers — **deferred** (backlog below).

---

## The five workstreams

Each: **Goal · Why · Tasks (with files) · Acceptance · Risk.**

### WS1 — PR-native gate + AIFactory-loop hardening  *(the wedge)*

- **Goal:** Every PR gets a **"TFactory / tests" status check** (red/green against a configurable quality bar) plus the triage report — making "Test" a merge gate in the tool devs already use.
- **Why:** Primary wedge + the agreed definition of "Review," delivered together. Highest adoption pull.
- **Tasks:**
  - `agents/quality_gate.py` (new) — compute pass/fail from `findings/verdicts.json` + `signals_summary` against a policy (e.g. accept-rate ≥ X, no SURVIVED mutants on changed lines, no high-risk flake, CI-parity ≠ `mocked-subject`). Policy read from `.tfactory.yml` (`quality_gate:` block).
  - `tools/pr_status.py` (new) — post a GitHub **commit status / Checks** result (mirror the dry-run-first pattern of `tools/pr_comment.py`); gated by `TFACTORY_PR_STATUS=1`.
  - `agents/triager.py` — call the gate + `pr_status` in the terminal-status hook, alongside the existing PR comment.
  - Harden + test the completion envelope / fixloop bound / idempotency (already real) — add regression tests + a `guides/pr-gate.md`.
- **Acceptance:** On a test PR, a "TFactory / tests" check turns **red when the gate fails, green when it passes**, links the triage report; threshold configurable in `.tfactory.yml`; dry-run by default.
- **Risk:** Low–medium (GitHub Checks needs a token with `checks:write`; fall back to commit-status if unavailable).

### WS2 — Generic-ingestion front door  *(cheap, widens TAM)*

- **Goal:** Run TFactory on a spec **without AIFactory** — upload a `.feature`/EARS/markdown file in the portal, or call an MCP tool.
- **Why:** The parsing already exists; only the door is missing. Small effort, removes the CLI-only gap, decouples TAM from AIFactory.
- **Tasks:**
  - MCP tool `task_create_from_spec` (in `mcp_server`/`tools_pkg`) — accept spec text/file + format, call `spec_sources.write_spec_markdown()` into a fresh workspace, schedule Planner. Reuse `task_create_and_run` plumbing **minus** the AIFactory snapshot.
  - **Target-mode** seam: with no diff, the SUT is a named file/module, not `base_ref..branch`. Coverage-delta baseline = "before this test" on the target. Add to `snapshotter.py` (no-diff path) + Planner context.
  - web-server `POST /api/specs/ingest` + frontend-web **"New test from spec"** upload (drag a `.feature`, pick target paths).
- **Acceptance:** Upload a `.feature` in the portal → task runs → triage report. Same headless via the MCP tool.
- **Risk:** Low (parsing reused); the only subtlety is target-mode coverage baseline.

### WS3 — Tenant hygiene: `projects`/`tasks` → DB + org-scoping  *(foundational)*

- **Goal:** Projects and tasks live in the DB (`Project`/`Task` models already exist, `models.py:256`) and are **org-scoped**, so "both deploy" doesn't become a rewrite.
- **Why:** The JSON-file `projects` route is the single biggest multi-tenant correctness hole. Cheaper to fix now than after SaaS launch.
- **Tasks:**
  - **Verify-first:** confirm current `routes/projects.py` storage + every workspace path keyed on `project_id`.
  - Rewrite `routes/projects.py` (and task routes) onto the DB models; gate with `require_org_role(...)` (the dependency already enforces membership/role for orgs).
  - One-shot migration `~/.tfactory` JSON → DB; preserve `DISABLE_AUTH`/single-user behaviour.
  - Reconcile `agent_service.py` worktree pathing with DB project ids.
- **Acceptance:** A user in org A cannot see org B's projects/tasks; existing local data migrated; single-user mode still works.
- **Risk:** **Highest** — touches the core data path + worktree pathing. Needs the most test coverage and a feature-flag rollout.

### WS4 — Java to production  *(prove "polyglot")*

- **Goal:** A Java subtask generates a JUnit test, runs sandboxed, and produces a **JaCoCo coverage-delta + PIT mutation verdict** end-to-end.
- **Why:** One new language done *properly* converts the "polyglot" claim from wedge to real; Java is the largest enterprise footprint.
- **Tasks:**
  - **Verify-first:** confirm the `tfactory-runner-java` image actually exists/builds (Maven + PIT + JaCoCo).
  - `agents/lang_java/gen.py` (new) — Java generation hook mirroring `lang_typescript`; the `frameworks/junit` descriptor + prompt context already exist.
  - Wire `lang_java/mutate_probe` `runner_fn` to **PIT** in Docker; hook `lang_java/jacoco_coverage` into the Evaluator's coverage signal (`evaluator.py` coverage dispatch).
  - Integration test: a Spring-style sample → generated JUnit → sandbox run → JaCoCo + PIT verdicts.
- **Acceptance:** End-to-end Java run with coverage-delta + mutation verdict in `verdicts.json`.
- **Risk:** Medium (JVM/Maven toolchain in the sandbox image; PIT runtime cost).

### WS5 — Procurement table-stakes: observability + compliance evidence

- **Goal:** Make the platform answer the first procurement questions: metrics/traces + a shippable SOC2/ISO/GDPR evidence pack.
- **Why:** SOC2 + ISO + GDPR were all flagged as gating; observability is asked on day one.
- **Tasks:**
  - Prometheus `/metrics` (web-server) + basic **OpenTelemetry** spans around the 4 pipeline stages.
  - Finish the **xfail** evidence (#160): `guides/compliance/soc2-evidence.md` (CC1–CC9/A1/C1), `dpia-data-flow.md`, `security/threat-model.md`; flip `tests/evidence/test_p7_evidence.py` xfail→pass.
- **Acceptance:** `/metrics` scrapeable; evidence tests pass (no xfail); a "compliance pack" doc set is complete.
- **Risk:** Low (mostly docs + a metrics middleware).

---

## Sequencing (solo → largely sequential, 13 weeks)

| Weeks | Focus | Rationale |
|---|---|---|
| **1–4** | **WS1** (PR gate + loop hardening) + **WS5a** (metrics — quick win) | Ship the wedge first; metrics piggyback. |
| **5–6** | **WS2** (generic-ingestion door) | Cheap, high-leverage, low risk — momentum before the risky one. |
| **7–10** | **WS3** (tenant hygiene) | Riskiest + foundational; needs the most room + tests. Behind a flag. |
| **11–13** | **WS4** (Java) + **WS5b** (compliance evidence) + buffer | Breadth proof + procurement pack; buffer absorbs WS3 overrun. |

If WS3 overruns (likely), **WS4 slips to next quarter** before anything else is cut — the wedge (WS1/WS2) and tenant-correctness (WS3) are the non-negotiables this phase.

## North-star metrics (how we know it's working)

- **Gate adoption:** % of PRs in pilot repos with the TFactory check enabled.
- **Trust:** mutation-kill rate + flake rate of *accepted* tests (the moat, quantified).
- **Time-to-green:** handover → triage report latency.
- **Breadth:** languages with a real end-to-end pass (target: 3 — Py, TS, Java).
- **Procurement:** SOC2/ISO/GDPR evidence complete; `/metrics` live.

## Explicitly NOT in this phase (backlog → next quarters)

C# + Go generation · GitLab/Bitbucket/Azure DevOps SCM abstraction · brownfield
repo-wide coverage-uplift mode · multi-tenant SaaS scale (job queue + horizontal
workers + cross-pod state) · massive CI fan-out (ephemeral runners) · IDE (VS Code)
"generate tests" action.

## Open risks / assumptions to validate first

- WS3 and WS4 each begin with a **verify-first** task — this plan assumes the
  audit's findings (JSON `projects` route; Java runner image present) but does not
  bet the sprint on them.
- The PR gate assumes a token with GitHub **Checks** permission; commit-status is
  the documented fallback.
- "Both deploy" is honoured by tenant-cleanliness (WS3), **not** by building SaaS
  scale this phase — that's a deliberate, owner-approved deferral.
