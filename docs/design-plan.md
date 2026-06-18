---
layout: default
title: Design Plan
permalink: /design-plan/
nav_order: 2
---

# TFactory Design Plan

> Author: design via super-brainstorm session
> Status: Shipped (v0.9.x) — this page records the design rationale and locked decisions.
> Originally locked: 2026-05-28 (kept for provenance)

---

## Context

TFactory ships as a governed node in the Factory line: **PFactory plans,
AIFactory builds, TFactory verifies, CFactory watches.** AIFactory is a
multi-agent autonomous coding platform built on the Claude Agent SDK; its
`/handover` flow hands a completed spec and branch over to TFactory for
verification.

TFactory (Test Factory) is purpose-built for autonomous test generation and
execution across five lanes — unit, browser, api, integration, and mutation —
with honest acceptance-criteria fidelity reporting. Security scanning (SAST,
deps, secrets, DAST) is delegated to dedicated pipelines and is out of scope for
TFactory.

**Triggering workflow:** AIFactory finishes a spec → handover to TFactory →
TFactory receives the spec + completed branch, generates tests aligned to
acceptance criteria, executes everything in a sandboxed per-task toolchain,
triages, and commits tests + posts a findings report back to the same PR.

---

## Landscape signals (May 2026)

- Canonical multi-agent QA pattern (independently converged on by OpenAI + Anthropic):
  **Planner → Generator → Executor → Evaluator → Triager.** Evaluator structurally
  separate from generators — self-evaluation is unreliable.
- **Mutation testing is the only signal that filters trivial assertions**
  (Meta's TestGen-LLM, DiffBlue, recent PRIMG paper). Non-negotiable for trust.
- **Hallucinated tests are the #1 failure mode** — ~39% of LLM-generated Python
  test failures are structural (imports of nonexistent utilities, fake methods).
  Mitigation: pre-flight compile/import checks + allow-listed APIs.
- **Flakiness** in LLM-generated tests is higher than human-written; unordered
  collection assumptions are the leading cause. Mitigation: static lint pass on
  generated test code.
- **Security autonomy is winning** (XBOW, PentAGI, DARPA AIxCC, CodeMender).
  Functional gen is harder than security in 2026.

---

## Locked design decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Repo strategy | **Hard fork** — TFactory began as a fork of AIFactory, then surgically reworked into its own service |
| 2 | Vision scope | **Five lanes** — unit, browser, api, integration, mutation (all active). Security scanning delegated to dedicated pipelines, out of scope |
| 3 | Handover payload | **Spec-aware** — `{project_id, spec_id, branch, base_ref}`; TFactory reads AIFactory's spec dir read-only |
| 4 | Agent topology | **Shared planner, per-lane generators, shared executor/evaluator/triager** (five agent roles: Planner, Gen-Functional/per-lane generators, Executor, Evaluator, Triager) |
| 5 | Execution env | **Per-task Nix toolchain in an ephemeral Kubernetes Job** (RFC-0005 Tier A) — repo checked out, network honest opt-in only |
| 6 | LLM routing | **Runs on any LLM via model-string routing** — provider-agnostic, no hard Anthropic dependency |
| 7 | Deliverable | **Auto-commit tests to AIFactory's feature branch + PR comment with report**; honest "verified X/Y" acceptance-criteria fidelity per criterion |
| 8 | Persistence | **`~/.tfactory/workspaces/{project_id}/specs/{spec_id}/`** mirroring AIFactory layout |
| 9 | Portal/UI | **Retheme inherited portal** — keep layout shell/auth/task list, drop spec-creation, add lane status, Acceptance and Evidence tabs (screenshots + Playwright video), and findings table |
| 10 | Credentials and MFA | **Credential Broker** (vault/sops/age/agenix, ephemeral, honest egress opt-in); MFA via TOTP (RFC-6238) + disposable ephemeral Keycloak (RFC-0007 Class C), zero production credentials |

---

## Architecture

### Component map

```
                            ┌─────────────────┐
                            │  AIFactory      │
                            │  finishes spec, │
                            │  pushes branch  │
                            └────────┬────────┘
                                     │
                  /handover-to-tfactory  (Claude Code skill)
                                     │
                                     v
                       mcp__tfactory__task_create_and_run
                                     │
                                     v
                            ┌────────────────┐
                            │ TFactory MCP   │
                            │ server         │   (stdio)
                            └────────┬───────┘
                                     │
                                     v
                            ┌────────────────┐
                            │ TFactory web   │
                            │ backend        │   FastAPI
                            └────────┬───────┘
                                     │ creates spec dir
                                     │ enqueues task
                                     v
                            ┌─────────────────┐
                            │  PLANNER agent  │
                            │  reads spec.md, │
                            │  diff, ac. crit │
                            └────────┬────────┘
                                     │ emits test_plan.json
                                     │ (lane-tagged subtasks)
                                     v
        ┌─────────┬─────────┬────────┼────────┬──────────┐
        │         │         │        │        │          │
        v         v         v        v        v
   ┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
   │Gen-unit ││Gen-     ││Gen-api ││Gen-     ││Gen-mut │
   │ pytest  ││browser  ││        ││integr.  ││ mutmut │
   │ (LLM)   ││Playwright││        ││         ││harness │
   └────┬────┘└────┬────┘└───┬────┘└───┬────┘└───┬────┘
        │          │         │         │         │
        └──────────┴─────────┼─────────┴─────────┘
                                    │
                                    v
                          ┌──────────────────┐
                          │    EXECUTOR      │ per-task Nix
                          │ ephemeral k8s    │ toolchain in an
                          │ Job (RFC-0005)   │ ephemeral Job
                          └────────┬─────────┘
                                   │
                                   v
                          ┌──────────────────┐
                          │   EVALUATOR      │ scores quality:
                          │ structurally     │  - coverage delta
                          │ separate         │  - mutation score
                          │                  │  - ac-fidelity X/Y
                          │                  │  - flake-risk lint
                          └────────┬─────────┘
                                   │
                                   v
                          ┌──────────────────┐
                          │    TRIAGER       │ dedup, rank, report
                          └────────┬─────────┘
                                   │
                                   v
        ┌──────────────────────────────────────────────────┐
        │  side-effects                                    │
        │   - git commit tests on AIFactory feature branch │
        │   - gh pr comment <pr> --body REPORT             │
        │   - write artifacts to ~/.tfactory/workspaces/   │
        │   - portal + CFactory cockpit show live status   │
        │   - screenshots + Playwright video as evidence   │
        └──────────────────────────────────────────────────┘
```

### Spec dir layout (per task)

```
~/.tfactory/workspaces/{project_id}/specs/{spec_id}/
  task.md                 # handover payload, agent-readable
  test_plan.json          # planner output, lane-tagged subtasks
  context/
    source.json           # { aifactory_spec_dir, branch, base_ref, sha }
    aifactory_spec.md     # snapshot copy of AIFactory's spec.md (read-only)
    aifactory_plan.json   # snapshot copy
    diff.patch            # base_ref..branch
    project_analysis.json # languages, frameworks, deps
  tests/                  # generated test artifacts pre-commit
    unit/
    browser/
    api/
    integration/
  findings/
    mutation.json
    ac_fidelity.json       # honest verified X/Y per criterion
  evidence/
    screenshots/           # browser lane
    video/                 # Playwright recordings
  report.md
  report.json
  logs/
    planner.log
    gen_functional.log
    executor.log
    evaluator.log
    triager.log
  memory/
    session_insights.json
```

### Repository layout

```
TFactory/
  apps/
    backend/
      agents/
        planner.py                # test planning
        gen_functional.py         # per-lane generators
        evaluator.py              # structurally separate
        triager.py
      prompts/
        planner.md
        gen_functional.md
        evaluator.md
        triager.md
      mcp_server/
        tfactory_server.py        # MCP tool surface
      tools/
        executor.py
        runners/
          lang_registry.py        # per-lang tool tables
      context/
        project_analyzer.py       # detects language, deps, frameworks
      providers/                  # model-string routing, any LLM
      test_plan/                  # plan models
      memory/
    web-server/                   # FastAPI portal backend
    frontend-web/                 # portal: lanes, Acceptance + Evidence tabs
  .claude/skills/
    handover-to-tfactory/         # handover skill
  .mcp.json
  scripts/
    start-tfactory-mcp.sh
    start-tfactory-portal.sh
  CLAUDE.md
```

The per-task execution toolchain is provisioned as a Nix flake inside an
ephemeral Kubernetes Job (RFC-0005 Tier A) rather than per-language Docker
images.

### Tooling table

| Lane | Python | TypeScript |
|---|---|---|
| Unit | `pytest` (+ unittest) | `vitest` (preferred) / `jest` |
| Browser | Playwright (screenshots + video) | Playwright (screenshots + video) |
| API | `pytest` + `httpx` | `vitest` + `supertest` |
| Integration | `pytest` | `vitest` / `jest` |
| Mutation | `mutmut` (default) / `cosmic-ray` | `stryker` |

The browser lane runs Playwright in the per-task Nix toolchain inside an
ephemeral Kubernetes Job and captures screenshots plus video recordings,
rendered in the portal Acceptance and Evidence tabs and the CFactory cockpit.
Security scanning (SAST, deps, secrets, DAST) is delegated to dedicated
pipelines and is out of scope for TFactory.

Per-language tool selection lives in `apps/backend/tools/runners/lang_registry.py`
so generator prompts can ask "what's the mutation tool for this project?" via MCP tool.

---

## What shipped

As of v0.9.x, TFactory runs the full pipeline end-to-end:

- **Five agents** — Planner, Gen-Functional / per-lane generators, Executor,
  Evaluator, Triager.
- **Five lanes** — unit, browser, api, integration, mutation — all active.
- **Browser lane** runs Playwright in a per-task Nix toolchain inside an
  ephemeral Kubernetes Job (RFC-0005 Tier A), capturing screenshots and video
  recordings rendered in the portal Acceptance and Evidence tabs and the
  CFactory cockpit.
- **Honest acceptance-criteria fidelity** — "verified X/Y" reported per
  criterion.
- **Credential Broker** (vault/sops/age/agenix, ephemeral, honest egress
  opt-in) with MFA via TOTP (RFC-6238) and disposable ephemeral Keycloak
  (RFC-0007 Class C); zero production credentials.
- **Provider-agnostic** — runs on any LLM via model-string routing.
- **Side-effects** — auto-commits tests to the AIFactory feature branch, posts
  the report as a PR comment, and delivers an RFC-0001 completion event
  at-least-once.
- A governed node in the Factory line: PFactory plans, AIFactory builds,
  TFactory verifies, CFactory watches.

Security scanning (SAST, deps, secrets, DAST) is out of scope — delegated to
dedicated pipelines.

---

## Verification plan

The end-to-end smoke that proves the pipeline works:

1. **Pick a real AIFactory spec.** Any small feature AIFactory has already
   shipped (e.g., a new endpoint + handler) gives a known-good spec dir.
2. **Run TFactory.** Start the portal; confirm it loads and the tasks list is
   empty.
3. **Trigger handover.** In a Claude Code session, run
   `/handover-to-tfactory --spec <spec_id>`. Expect:
   - Task appears in `~/.tfactory/workspaces/<project_id>/specs/<new_id>/`.
   - `task.md`, `context/source.json`, snapshot of AIFactory spec, diff written.
   - Portal shows it pending → planning → generating → running →
     evaluating → triaging → done.
4. **Check generated tests.** `cd <project> && git log --oneline` — shows a
   new commit `tfactory: tests for <spec_id>` with files under `tests/`.
5. **Run the generated tests against current code.** They pass.
6. **Mutate one line of the feature code, rerun.** At least one generated test
   fails (proves tests have signal).
7. **Check PR comment.** `gh pr view <pr> --comments` — TFactory's report is
   there, summarising coverage delta, mutation score, acceptance-criteria
   fidelity (verified X/Y), and flake-lint warnings if any.
8. **Hallucination guard test.** Feed Planner a spec referring to a method that
   doesn't actually exist in the diffed code. Gen-Functional detects it
   via the pre-flight check and asks Planner to revise, rather than emit a
   hallucinated test.
9. **Failure-path test.** Disrupt the ephemeral Kubernetes Job mid-task. The
   task fails gracefully with a clear error in `report.md` and status
   `failed`, not a hang.

---

## Risks and mitigations

| Risk | Mitigation in this design |
|---|---|
| LLM tests hallucinate imports / methods (~39% of failures) | Pre-flight static check in Gen-Functional: every `import` and every method call resolves against the project's actual symbols before commit. Reject + replan if not. |
| LLM tests are trivial (assert True, no mutation kills) | The mutation lane is the gate — tests with zero kills are flagged or rejected. Acceptance-criteria fidelity is reported honestly as verified X/Y per criterion. |
| LLM tests are flaky | Evaluator reruns each new test; static lint for unordered-collection + timing patterns. |
| Generated test code does something nasty (deletes files, exfiltrates) | Per-task Nix toolchain in an ephemeral Kubernetes Job; network is honest opt-in only. Credentials are brokered ephemerally (vault/sops/age/agenix) with zero production credentials and MFA via TOTP + disposable ephemeral Keycloak. |
| AIFactory spec schema changes break TFactory | TFactory snapshots AIFactory's spec into its own `context/` at handover time — TFactory operates on the snapshot, not a live reference. Schema check at the read boundary. |
| Cost runaway on long specs | Per-task token budget cap enforced at Executor layer; planner instructed to scope to diff lines only. |
| AIFactory and TFactory infra modules drift | Acknowledged trade-off of the hard-fork origin — divergence could later be reconciled by extracting a `factory-core` package if it becomes painful. |
