---
layout: default
title: TFactory
nav_order: 1
---

<section class="hero">
  <span class="hero__eyebrow">v0.9 · five-lane pipeline · reproducible Nix execution · visible screenshot + recording evidence · acceptance-criteria fidelity · MFA-authenticated testing · runs on any LLM</span>
  <h1 class="hero__title">Autonomous tests, AI-graded.</h1>
  <p class="hero__subtitle">
    Hand TFactory a finished feature on a branch — from AIFactory, Claude Code,
    or any tool, via the MCP control plane or a plain acceptance-criteria file
    (markdown / Gherkin / EARS). The agent pipeline plans, writes, sandboxes,
    scores, and commits the suite — autonomously — grades every acceptance
    criterion against a test that actually ran, and posts a triage report to
    your PR.
  </p>
  <p>
    <a class="hero__cta hero__cta--primary" href="{{ '/showcase/' | relative_url }}">
      See the demo
    </a>
    &nbsp;
    <a class="hero__cta hero__cta--ghost" href="{{ '/design-plan/' | relative_url }}">
      Design plan
    </a>
  </p>
</section>

{% include stat-grid.html %}

{% include pipeline-diagram.html %}

> **Part of the [Factory family](https://factory.freundcloud.com/)** — a governed, verified, observable autonomous software factory. [PFactory](https://pfactory.freundcloud.com/) plans, [AIFactory](https://aifactory.freundcloud.com/) builds, **TFactory** verifies, [CFactory](https://github.com/olafkfreund/CFactory) watches over all four. See **[Why Factory](https://factory.freundcloud.com/why/)**.

## How it works

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <h3>Spec-aware handover</h3>
    <p>A Claude Code session in your AIFactory repo runs <code>/handover-to-tfactory</code>, or any tool posts acceptance criteria through the MCP control plane. TFactory snapshots the signed contract and the deployed URL, runs five agents, and returns a verdicts report.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <h3>Two-layer guardrails</h3>
    <p>A pre-flight static check confirms every <code>import</code> resolves. Flake-risk lint catches dict-iteration order, <code>time.sleep</code>, and <code>datetime.now</code> without a freeze.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <h3>Five-signal verdicts</h3>
    <p>Coverage delta, a 3x stability re-run, a mutate-and-check probe, flake-lint promotion, and LLM semantic relevance. Tests that survive a mutation do not ship.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 240ms">
    <h3>Dry-run by default</h3>
    <p>Per the no-auto-push policy, the git writer and PR commenter record their commands without executing. Operators opt in explicitly.</p>
  </li>
</ul>

## Evidence you can see

A green checkmark is not proof. For interactive acceptance criteria, the browser
lane runs in a reproducible per-task Nix toolchain inside an ephemeral Kubernetes
Job (RFC-0005 Tier A), drives the real deployed app, and captures a screenshot of
the rendered page plus a recording of the test driving it. The **Acceptance** tab
grades each criterion against a test that actually passed — an honest
"verified X/Y", never a blanket "done":

![The Acceptance tab — verified 5/5 acceptance criteria, each linked to its evidence]({{ '/static/img/screenshots/portal-acceptance.png' | relative_url }})

The **Evidence** tab renders the captured recordings and screenshots inline, so a
reviewer can watch the test execute and look at the page it produced:

![The Evidence tab — browser-lane recordings and screenshots]({{ '/static/img/screenshots/portal-evidence-recordings.png' | relative_url }})

The whole pipeline — Plan, Generate, Execute, Report — is a live view in the
portal, and the same evidence appears on the finished task in the
[CFactory](https://github.com/olafkfreund/CFactory) cockpit:

![The TFactory pipeline view — Plan, Generate, Execute, Report]({{ '/static/img/screenshots/portal-pipeline.png' | relative_url }})

## Reach anything under test — including behind MFA

<ul class="feature-row">
  <li class="feature-row__card reveal">
    <h3>Authenticated and 2FA targets</h3>
    <p>The <code>.tfactory.yml</code> auth schema covers form, API-token, basic-auth and <strong>TOTP two-factor</strong> credentials with an ordered login-step flow. For MFA we do not bypass anything: the pipeline provisions a disposable identity provider, owns the OTP secret, generates valid RFC-6238 codes at fill time, captures the authenticated page, and tears the IdP down — zero production credentials. <a href="{{ '/credentials/' | relative_url }}">See how</a></p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <h3>Credential Broker</h3>
    <p>Resolve secrets from a vault (Azure KV, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) or local sops / age / agenix, materialise them ephemerally (0600, wiped per task), gated by an explicit egress opt-in with an honest manifest. <a href="{{ '/credentials/' | relative_url }}">See how</a></p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <h3>Kubernetes and SaaS</h3>
    <p>Log-in-once browser sessions (storageState), <code>type: kubernetes</code> port-forward targets, and first-class <code>type: connector</code> SaaS targets (ServiceNow / Salesforce / SAP / MuleSoft). <a href="{{ '/examples/' | relative_url }}">See examples</a></p>
  </li>
</ul>

## A governed node in the Factory line

<ul class="feature-row">
  <li class="feature-row__card reveal">
    <h3>Governed pickup from PFactory</h3>
    <p>TFactory enqueues governed test targets from <a href="https://pfactory.freundcloud.com/">PFactory</a>, parses the planned acceptance contract as the test oracle, then generates, runs, and reports back up the spine. The contract — signed, with the deployed URL — travels with the work.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <h3>One completion event</h3>
    <p>The Triager emits a normalized <a href="https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0001-correlation-key-and-completion-event.md">RFC-0001</a> completion event with a shared <code>correlation_key</code>, delivered at-least-once via a durable outbox and idempotency key, so the whole line speaks one schema and CFactory watches a single contract. <a href="{{ '/completion-event-envelope/' | relative_url }}">See the envelope</a></p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <h3>In the Backstage catalog</h3>
    <p>TFactory ships a <code>catalog-info.yaml</code> plus TechDocs and is importable into Backstage, with enriched annotations and an AI-assistant skill descriptor — discoverable alongside the rest of the Factory.</p>
  </li>
</ul>

## Status by lane

<div class="reveal" markdown="1">

The lane spine is modality-based (Decision 2). Security scanning is delegated to
dedicated security pipelines and is out of scope here — TFactory focuses on
functional and feature testing.

| Lane | Status | Runtime | Coverage | Evidence captured |
|---|---|---|---|---|
| **Unit** | Active | `tfactory-runner-pytest` + `tfactory-runner-jest` | line (cobertura / lcov) | — |
| **Browser** | Active | Nix toolchain in a k8s Job (Playwright); host fallback where applicable | n/a (line coverage doesn't apply when the test drives the browser) | screenshots, video, trace |
| **API** | Active | per-framework image + HTTP HAR recorder | line where the test exercises framework code | network.har |
| **Integration** | Active | per-framework image + AppRuntime (multi-service) | line where applicable | network.har, service logs |
| **Mutation** | Active | `mutmut` (Python) / Stryker (TypeScript) — one-mutation-per-run probe in the Evaluator | reported per mutant (killed / survived) | — |

Each subtask's lane is chosen by the Planner from its `(language, framework)` via
the [framework registry](framework-registry/); reviewers see the lifecycle phases
in the LaneStatusGrid and the per-test evidence in the Triager PR comment. New
languages and additional pipelines hook into the same five-lane spine through new
`FrameworkDescriptor`s — no lane additions required.

</div>

## Quickstart

<div class="reveal" markdown="1">

```bash
# Clone and bootstrap (NixOS / flake-based)
git clone https://github.com/olafkfreund/TFactory
cd TFactory
nix develop
tfactory-minimal-venv   # creates apps/backend/.venv
tfactory-test           # backend suite, seconds
```

Full walkthrough in the [repo README](https://github.com/olafkfreund/TFactory/blob/main/README.md) plus the [end-to-end smoke guide](https://github.com/olafkfreund/TFactory/blob/main/guides/e2e-smoke.md) for running real scenarios against an AIFactory project.

</div>

## Documentation

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <h3><a href="{{ '/architecture/' | relative_url }}">Architecture</a></h3>
    <p>Directory structure, workspace layout, dataflow.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 70ms">
    <h3><a href="{{ '/design-plan/' | relative_url }}">Design plan</a></h3>
    <p>Locked decisions, landscape research, risk register.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 140ms">
    <h3><a href="{{ '/showcase/' | relative_url }}">Showcase</a></h3>
    <p>The pipeline in action, with real captured evidence.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 210ms">
    <h3><a href="{{ '/technical-spec/' | relative_url }}">Technical spec</a></h3>
    <p>Per-component implementation detail.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 280ms">
    <h3><a href="{{ '/credentials/' | relative_url }}">Credentials and MFA</a></h3>
    <p>The Credential Broker, authenticated targets, and 2FA testing.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 350ms">
    <h3><a href="{{ '/tests/' | relative_url }}">Test coverage</a></h3>
    <p>The TDD plan: unit / integration / e2e pyramid.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 420ms">
    <h3><a href="{{ '/progress/' | relative_url }}">Progress</a></h3>
    <p>The live build log: closed tasks and commits.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 490ms">
    <h3><a href="https://github.com/olafkfreund/TFactory/blob/main/CHANGELOG.md">Changelog</a></h3>
    <p>Release notes and history.</p>
  </li>
</ul>

## Tracking

- **Epic and sub-issues** — [github.com/olafkfreund/TFactory/issues](https://github.com/olafkfreund/TFactory/issues)
- **Source** — [github.com/olafkfreund/TFactory](https://github.com/olafkfreund/TFactory)
- **Sister project (upstream)** — [github.com/olafkfreund/AIFactory](https://github.com/olafkfreund/AIFactory)
- **License** — [MIT OR GPL-3.0](https://github.com/olafkfreund/TFactory/blob/main/LICENSE)
