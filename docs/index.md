---
layout: default
title: TFactory
nav_order: 1
---

<section class="hero">
  <span class="hero__eyebrow">v0.1.0-mvp · walking skeleton complete</span>
  <h1 class="hero__title">Autonomous tests, AI-graded.</h1>
  <p class="hero__subtitle">
    Hand TFactory a finished AIFactory branch. The four-agent pipeline plans,
    writes, sandboxes, scores, and commits a pytest suite — autonomously —
    then posts a triage report to your PR.
  </p>
  <p>
    <a class="hero__cta" href="https://github.com/olafkfreund/TFactory/releases/tag/v0.1.0-mvp">
      v0.1.0-mvp release ↗
    </a>
    &nbsp;
    <a class="hero__cta hero__cta--ghost" href="{{ '/design-plan/' | relative_url }}">
      Design plan →
    </a>
  </p>
</section>

{% include stat-grid.html %}

{% include pipeline-diagram.html %}

## How it works

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <span class="feature-row__icon" aria-hidden="true">🪶</span>
    <h3>Spec-aware handover</h3>
    <p>A Claude Code session in your AIFactory repo runs <code>/handover-to-tfactory</code>. TFactory snapshots the spec + diff, runs four agents, returns a verdicts report.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <span class="feature-row__icon" aria-hidden="true">🛡️</span>
    <h3>Two-layer guardrails</h3>
    <p>Pre-flight static-checks every <code>import</code> resolves. Flake-risk lint catches dict-iteration order, time.sleep, datetime.now without freeze.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <span class="feature-row__icon" aria-hidden="true">🔬</span>
    <h3>5-signal verdicts</h3>
    <p>Coverage delta · 3× stability re-run · mutate-and-check probe · flake-lint promotion · LLM semantic relevance. Survived-mutation tests don't ship.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 240ms">
    <span class="feature-row__icon" aria-hidden="true">📨</span>
    <h3>Dry-run by default</h3>
    <p>Per <code>CLAUDE.md</code> no-auto-push policy: git_writer + gh pr comment record argvs without executing. Operators opt in via env vars.</p>
  </li>
</ul>

## Status by lane

<div class="reveal">

| Lane | Status | Phase |
|---|---|---|
| **Functional (pytest)** | ✅ Active at MVP | 1 |
| SAST + deps + secrets | 🔜 Coming | 2 |
| DAST | 🔜 Coming | 3 |
| Fuzz | 🔜 Coming | 4 |
| Mutation testing | 🔜 Coming | 5 |
| Go / Rust / Ruby ramp | 🔜 Coming | 6+ |

</div>

## Quickstart

<div class="reveal">

```bash
# Clone + bootstrap (NixOS / flake-based)
git clone https://github.com/olafkfreund/TFactory
cd TFactory
nix develop
tfactory-minimal-venv   # creates apps/backend/.venv
tfactory-test           # 531 backend tests, ~10s
```

Full walkthrough in the [repo README](https://github.com/olafkfreund/TFactory/blob/main/README.md) plus the [end-to-end smoke guide](https://github.com/olafkfreund/TFactory/blob/main/guides/e2e-smoke.md) for running real scenarios against an AIFactory project.

</div>

## Documentation

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <span class="feature-row__icon">🏗️</span>
    <h3><a href="{{ '/architecture/' | relative_url }}">Architecture</a></h3>
    <p>Directory structure, workspace layout, dataflow.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 70ms">
    <span class="feature-row__icon">🧭</span>
    <h3><a href="{{ '/design-plan/' | relative_url }}">Design plan</a></h3>
    <p>10 locked decisions, landscape research, risk register.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 140ms">
    <span class="feature-row__icon">📜</span>
    <h3><a href="{{ '/spec/' | relative_url }}">Spec</a></h3>
    <p>Agent OS spec: overview, user stories, deliverables.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 210ms">
    <span class="feature-row__icon">🔧</span>
    <h3><a href="{{ '/technical-spec/' | relative_url }}">Technical spec</a></h3>
    <p>Per-component implementation detail.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 280ms">
    <span class="feature-row__icon">🧪</span>
    <h3><a href="{{ '/tests/' | relative_url }}">Test coverage</a></h3>
    <p>TDD plan: unit / integration / e2e pyramid.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 350ms">
    <span class="feature-row__icon">📋</span>
    <h3><a href="{{ '/tasks/' | relative_url }}">Tasks</a></h3>
    <p>All 12 MVP tasks, dependency graph, GitHub issues.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 420ms">
    <span class="feature-row__icon">📈</span>
    <h3><a href="{{ '/progress/' | relative_url }}">Progress</a></h3>
    <p>Live build log, closed tasks + commits.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 490ms">
    <span class="feature-row__icon">⚡</span>
    <h3><a href="https://github.com/olafkfreund/TFactory/blob/main/CHANGELOG.md">Changelog</a></h3>
    <p>v0.1.0-mvp release notes, deferred work, sharp edges.</p>
  </li>
</ul>

## Tracking

- **Epic + sub-issues** → [github.com/olafkfreund/TFactory/issues](https://github.com/olafkfreund/TFactory/issues)
- **Source** → [github.com/olafkfreund/TFactory](https://github.com/olafkfreund/TFactory)
- **Sister project (upstream)** → [github.com/olafkfreund/AIFactory](https://github.com/olafkfreund/AIFactory)
- **License** → [MIT OR GPL-3.0](https://github.com/olafkfreund/TFactory/blob/main/LICENSE)
