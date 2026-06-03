---
layout: default
title: Examples
permalink: /examples/
nav_order: 5
---

# Real-life examples

> Concrete walk-throughs for every TFactory capability — the situation, the
> command, and what you get back. These mirror the actual skills and CLIs; swap
> in your own project/spec ids. For the pipeline running live end-to-end, see
> [the showcase]({{ '/showcase/' | relative_url }}) and [demos]({{ '/demos/' | relative_url }}).

TFactory's one job: **test quality, not test count**. Every example below is a
real way teams use that to ship with confidence.

```
Planner → Gen-Functional → Executor → Evaluator → Triager
```

## 1. Hand a finished feature off for tests

**Situation.** You just merged a "reset password" feature on a branch and want
aligned tests + an honest verdict before the PR goes out — without writing them
by hand.

```
/handover-to-tfactory
```

TFactory snapshots the spec, the Planner emits lane-tagged subtasks across the
five lanes, the generators write tests, the Executor runs them in a sandbox, the
Evaluator scores each with the **5 signals** (coverage delta · 3× stability ·
mutate-and-check · flake-lint · semantic relevance), and the Triager produces
`findings/triage_report.md`. You get accepted tests committed to the branch
(dry-run by default) + a PR comment with evidence links.

**What you get.** A coverage/verdict report you can trust — flaky and trivially-
passing tests are flagged or rejected, not counted.

## 2. Close the loop — fix what the tests found (v0.5.0)

**Situation.** The run above came back with a failing api test: the handler
returns `500` on a valid reset token. You don't want to file a ticket — you want
it fixed and re-tested.

```
/handback-to-aifactory          # preview the correction, then send
/loop 60s /tfactory-fixloop <task_id>   # or: drive it hands-off
```

TFactory packages the failures into a `QA_FIX_REQUEST.md`, AIFactory's QA Fixer
patches the code on the *same* spec, then TFactory re-tests. The loop is
**bounded** — it stops at green, or `stuck` (a cycle cap, or the same tests
still failing) so a human steps in rather than churning.

**What you get.** test → fix → re-test as one thread, with a hard stop.

## 3. Test a UI flow and capture what a human would check

**Situation.** Your feature is a multi-step checkout. Assertions alone won't tell
you the confirmation page *looks* right.

```
# at handover, enable a visual inspection:
visual_inspection = { enabled: true, target: "storefront", flow: "add to cart → checkout → confirm" }
```

TFactory records a real Playwright run — **trace + video + step-labelled
verification and error screenshots** — and packages a human report + an LLM
correction plan into `automated-test/<datetime>/`, committed to the repo and
shown in the portal's **Visual Reports**.

**What you get.** Evidence, not adjectives — screenshots of each step, and a
plan when something's off.

## 4. Assess a cloud account's posture (AWS · GCP · Azure)

**Situation.** Before a release you want to know if the account drifted — public
buckets, over-broad IAM, unencrypted volumes.

```
/cloud-discover            # or portal: +Task → Cloud Infrastructure
```

A **read-only** flow: access gate → discovery → Mermaid topology → Prowler/CIS
scan (OCSF) → an accept/flag/reject verdict → a downloadable remediation plan.
All three providers are live-verified read-only; nothing is changed.

**What you get.** A posture report in **Cloud Reports** with the exact
misconfigurations and how to fix them — distinct from app-code testing.

## 5. Test a service that needs login

**Situation.** The SUT is behind auth; a test has to sign in first.

```yaml
# .tfactory.yml
targets:
  - name: app
    type: http
    base_url: https://staging.example.com
    auth: { type: ref, credentials: staging-user }   # resolved from the vault
```

For browser lanes, Gen-Functional scaffolds `auth.setup.ts` so the test **logs
in once and reuses the session** (storageState, #107). The credential is
resolved by the **Credential Broker**, injected egress-gated, and **wiped after
the run** — it never touches the repo.

**What you get.** Authenticated tests without secrets in code.

## 6. Reach a service inside Kubernetes (#108)

**Situation.** The API under test only exists inside the cluster.

```yaml
# .tfactory.yml
targets:
  - name: billing
    type: kubernetes
    context: staging
    namespace: payments
    service: billing
    port: 8080
    port_forward: true
    auth: { type: serviceaccount }     # read-only kubeconfig
```

TFactory `kubectl port-forward`s the service for the run lifetime, injects
`http://localhost:<port>` as `TFACTORY_TARGET_URL`, and tears the tunnel down on
success **and** failure. Live-verified against a real cluster.

**What you get.** api/browser tests against in-cluster services, no manual
tunnels.

## 7. Test a SaaS platform (ServiceNow / Salesforce / SAP / MuleSoft) (#111)

**Situation.** Your "feature" is a ServiceNow workflow, not a repo of code.

```yaml
# .tfactory.yml
targets:
  - name: snow
    type: connector
    platform: servicenow
    base_url: https://acme.service-now.com
    auth: { type: ref, credentials: snow-svc }
```

A first-class `type: connector` target reuses the http + credential-vault auth;
the platform registry maps each platform to its API style and a starter check
template.

**What you get.** TFactory as a test harness for SaaS, not just source code.

## 8. Run on your own LLM — local or air-gapped

**Situation.** Compliance says no data leaves the network.

```
python apps/backend/byo_llm.py <model>   # exits 0 only if the run stays local
```

TFactory runs on the Claude Agent SDK by default but also Codex CLI, GitHub
Copilot CLI, Gemini CLI, **Ollama**, and any OpenAI-compatible endpoint (LM
Studio, vLLM, …). The BYO-LLM classifier shows an honest **"🔒 Local — no data
egress"** badge so you can prove it.

**What you get.** The same pipeline on a model you control.

## 9. Use TFactory without AIFactory (any AC source)

**Situation.** Your acceptance criteria live in a markdown doc or a Gherkin
`.feature`, and you don't use AIFactory.

```bash
python apps/backend/spec_sources.py acceptance.feature --context <spec_dir>/context
```

`spec_sources.py` ingests markdown / Gherkin / EARS and normalises it into the
canonical spec the Planner reads — then hand off exactly as in example 1.

**What you get.** TFactory as a standalone test-generation platform.

## Where next

- [Showcase]({{ '/showcase/' | relative_url }}) — a live end-to-end run with a seeded bug
- [Demos]({{ '/demos/' | relative_url }}) — scenario recordings across the lanes
- [Architecture]({{ '/architecture/' | relative_url }}) — how the agents fit together
- [Progress]({{ '/progress/' | relative_url }}) — what shipped, release by release
