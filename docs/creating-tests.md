---
layout: default
title: Creating tests
nav_order: 4
---

# Creating tests

There are three ways to get a working test plan into TFactory: write the spec
yourself (from a file or a GitHub issue), use the portal's task wizard, or hand a
task over from your AI coding tool. All three converge on the same thing — a
**spec with acceptance criteria** that the planner turns into a `test_plan.json`,
which the five-lane pipeline executes and grades. This page covers what each path
needs and why the handover path matters.

> The pipeline is the same regardless of entry point: Planner → per-lane
> Generators → Executor → Evaluator → Triager. What differs is only how the spec
> and its parameters arrive.

## 1. Manually, from a spec or a GitHub issue

### The acceptance criteria are the oracle

TFactory generates tests to verify **declared acceptance criteria** — it never
guesses what "tested" means. A spec is markdown (or Gherkin `.feature` / EARS,
see [`guides/spec-sources.md`](https://github.com/olafkfreund/TFactory/blob/main/guides/spec-sources.md))
whose criteria live under an `## Acceptance Criteria` heading, one `AC#N` per
line:

```markdown
# Add CSV export to the reports page

## Acceptance Criteria
- AC#1: The Reports page shows an "Export CSV" button
- AC#2: Clicking it downloads reports_YYYY-MM-DD.csv
- AC#3: The CSV contains every visible row and column
- AC#4: An empty report still returns a valid CSV header
```

When the work comes from a GitHub issue, it is the same contract: PFactory emits a
**governed issue** whose acceptance criteria are the oracle, and the
[RFC-0001 correlation key](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0001-correlation-key-and-completion-event.md)
(the issue number) threads the work across the line. Each criterion must map to a
child of the work — a criterion with no covering test is reported, not hidden.

### `.tfactory.yml` — the test-target parameters

The acceptance criteria say *what* to verify; `.tfactory.yml` (at the repo root)
says *what to run it against*. The full schema is in
`apps/backend/tfactory_yml/schema.py`. Top-level keys:

| Key | Type | Meaning |
|---|---|---|
| `version` | `1` | Schema version (required). |
| `targets` | list | The systems under test (see target types below). Required. |
| `default_target` | string | Which target the lanes use unless overridden. |
| `test_credentials` | map | Named credentials a target's `auth` references (never inline secrets). |
| `egress` | object | `enabled: false` by default; must be opted in for any network/login. |
| `build` | list | Build steps to run before testing (compile, migrate). |
| `test_data` | object | Seed/teardown commands for integration data. |
| `evidence_policy` | object | What evidence to capture/retain (screenshots, video, HAR). |
| `credentials` | map | Non-target credentials exposed to agents (env/file). |
| `quality_gate` | object | Thresholds the verdict must clear. |

**Target types** (`targets[].type`): `http` (browser/api), `kubernetes`
(port-forward), `docker_compose` / `docker_run` (bring the app up), `connector`
(ServiceNow / Salesforce / SAP / MuleSoft), `cloud_provider` (read-only AWS / GCP
/ Azure posture), `feature_flag`. An `http` target takes `name`, `base_url`, an
optional `auth`, `health_check`, `openapi_spec` (API-lane context), and
`visual: true` to force the browser/visual lane.

**Auth** (`targets[].auth.type`): `bearer`, `basic`, `oauth2_client_credentials`,
`service_account`, `mtls`, `none`, or `ref` (a multi-step browser login that
points at a `test_credentials` entry).

**Credentials** (`test_credentials.<name>`): `ref` (a broker reference like
`env:NAME` or a vault ref — never the literal secret), `as_secret` /
`as_username` (the env var names the login reads), and `kind` ∈
`form` / `api_token` / `basic_auth` / `totp`. For two-factor logins, a second ref
carries the seed: `totp_ref` + `as_totp_secret`, with optional `totp_digits`
(6), `totp_algorithm` (sha1/sha256/sha512), `totp_period` (30). See
[Credentials and MFA]({{ '/credentials/' | relative_url }}).

A minimal `.tfactory.yml` for a browser + api run behind a form login with 2FA:

```yaml
version: 1
egress:
  enabled: true            # required for a live login
targets:
  - name: app
    type: http
    base_url: https://staging.example.com
    auth:
      type: ref
      ref: app-login
      steps:
        - { action: goto, url: "https://staging.example.com/login" }
        - { action: fill_username, selector: "#username" }
        - { action: fill_secret,   selector: "#password" }
        - { action: click,         selector: "#kc-login" }
        - { action: fill_totp,     selector: "#otp" }
        - { action: click,         selector: "#kc-login" }
        - { action: wait_for_url,  url: "account" }
test_credentials:
  app-login:
    kind: totp
    ref: env:APP_PASSWORD
    as_secret: APP_PASSWORD
    username_ref: env:APP_USERNAME
    as_username: APP_USERNAME
    totp_ref: env:APP_TOTP_SEED
    as_totp_secret: APP_TOTP_SEED
```

### The signed contract (when it comes through the line)

When AIFactory hands a built branch to TFactory, the spec arrives inside a signed
[Task Contract](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0002-task-contract.md).
Its `tfactory` block carries the run parameters — `lanes`, `frameworks`
(lane → framework), `ac_to_code_map` (which files each criterion covers),
`coverage_target`, `mutation_scope` — and its `environment` block carries the
toolchain and `serve_command` (e.g. `python -m uvicorn app:app --port 8099`) plus
the **deployed URL**. TFactory tests the *declared* criteria against the *real*
deployment, and the lane choices are authoritative — not inferred.

## 2. In the portal — the task wizard

Click **+ Task** in the portal to open the task-creation wizard
(`TaskCreationWizard.tsx`). You provide:

- **Description** — the spec / feature text, including the `## Acceptance
  Criteria`. This is the main input; `@` references files in the repo.
- **Title** — optional; auto-generated from the description if left empty.
- **Category, priority, complexity, impact** — routing hints.
- **Profile / model / thinking level** — which LLM provider and depth to run
  (per-phase model overrides are available).
- **Skills, base branch, "require review before coding"** — optional controls.

Submitting posts to `POST /api/projects/{project_id}/tasks`, which writes the spec
into `.tfactory/specs/` and the planner **auto-runs on ingest** — so the task
moves straight into planning without a separate step. You then watch it advance
through the pipeline in the portal (Plan → Generate → Execute → Report) and review
the evidence on the task's Acceptance and Evidence tabs.

## 3. Handover from your AI coding tool

### What it does

From a Claude Code session in your repo, `/handover` snapshots the task and hands
it to TFactory to run asynchronously. The skill
(`.claude/skills/handover/SKILL.md`) wraps the MCP tool
`mcp__tfactory__task_create_and_run`; the snapshot seeds the spec (the planner
then writes `spec.md` and `test_plan.json`). After handover the task advances on
its own through the human gates:

```
created -> planning -> human_review -> coding -> qa -> done
```

You approve the plan from the portal or from chat
(`mcp__tfactory__task_approve_plan`), and a draft PR appears when the build
completes. The same `mcp__tfactory__*` control plane (around 15 stdio tools —
`task_list`, `task_status`, `task_get_logs`, `task_start`, `task_create_pr`, …;
see [`guides/CLAUDE_CODE_MCP_TOOLS.md`](https://github.com/olafkfreund/TFactory/blob/main/guides/CLAUDE_CODE_MCP_TOOLS.md))
lets you drive everything without leaving the editor. Destructive tools
(`task_create_and_run`, `task_create_pr`, `task_merge_pr`, `task_recover`)
require `confirm=true`, so an autonomous agent can't kick off a paid run or merge
a PR unprompted.

### Why it matters

The point of handover is that the **acceptance contract travels with the work**.
The criteria, the lane choices, the deployed URL, and (down the line) the signed
provenance all move together, so TFactory tests exactly what was agreed against
exactly what was deployed — autonomously, audit-traceable, and self-hosted, using
the same planner → coder → QA → review pipeline interactive users already trust.

### From Antigravity, Codex, and other tools

Two things are easy to conflate, so to be precise:

- **Which model runs the pipeline** is independent of how you hand off. TFactory
  routes each phase to a provider purely from the model string — Claude, Gemini
  (Antigravity), OpenAI Codex, Copilot CLI, Ollama, or any OpenAI-compatible
  endpoint. So a run started any way can *execute* on Gemini or Codex. See
  [Run on any LLM](https://github.com/olafkfreund/TFactory/blob/main/guides/byo-llm.md).
- **The polished `/handover` entry point is Claude-Code-scoped today.** Other
  tools hand off through the tool-agnostic paths instead: the portal task wizard
  (path 2) or the REST front door (`POST /api/specs/ingest`, which accepts
  markdown / Gherkin / EARS with no AIFactory required). A remote-MCP
  `create_and_run` for other IDEs and a `tfactory handover` CLI are on the
  roadmap, not yet shipped — so for Antigravity / Codex / Cursor today, use the
  portal or the REST ingest, and select the provider via the model string.

## See also

- [Credentials and MFA]({{ '/credentials/' | relative_url }}) — the Credential Broker, authenticated targets, and 2FA.
- [Examples]({{ '/examples/' | relative_url }}) — end-to-end scenarios.
- [Architecture]({{ '/architecture/' | relative_url }}) — the five-lane pipeline.
