---
layout: post
title: "v0.9.0: the enterprise foundation"
subtitle: "A merge gate, a no-AIFactory front door, multi-tenant groundwork, and the start of real polyglot — without diluting the one thing that matters: proving a generated test is worth keeping."
date: 2026-06-10
author: DataSeek Team
---

An LLM will write you a thousand tests. That number means nothing. The question
that decides whether a test suite is an asset or a liability is the one almost
no tool answers: *is this generated test actually worth keeping* — or is it a
tautology that passes today and lies tomorrow?

That question is TFactory's whole job, and it stays the centre of v0.9.0. What
this release adds is everything **around** it that an enterprise needs before
the answer can matter: a place to enforce it, a way in that doesn't assume our
sister tool, the start of tenancy, and a second real language.

## The verdict, where merges happen

The moat was never generation — it's the **verdict pipeline**: a generated test
has to clear coverage-delta, **3× stability**, a **mutation-kill** probe, and
**CI-parity** before it counts.

```
Planner → Gen-Functional → Executor → Evaluator → Triager
```

v0.9.0 puts that verdict where decisions are actually made. The Triager can now
publish a red/green **`TFactory / tests`** status on the pull request, graded
against a `quality_gate` policy you set in `.tfactory.yml` — minimum accept-rate,
no surviving mutants, no tests that pass only by mocking out the thing they test.
It's opt-in and dry-run-first; turn it on and "did the tests hold up" becomes a
merge gate in the workflow your team already runs.

## A front door that doesn't need AIFactory

TFactory grew up taking finished features from AIFactory. It no longer has to.
A new `task_create_from_spec` tool and a `POST /api/specs/ingest` endpoint take a
raw acceptance-criteria spec — **markdown, Gherkin, or EARS** — and turn it into
a native test-generation task. No branch, no handover.

The same seam fixed an inherited wart: creating a task from a GitHub issue in the
portal used to mint an *AIFactory coding task*. Now it creates a native TFactory
test task, through the ingest endpoint, where it belongs.

## Multi-tenant, before it's a rewrite

Enterprise means tenancy, and retrofitting it late is how you earn a quarter of
pain. So v0.9.0 lays the groundwork now: a migration of legacy project metadata
into the database, a `ProjectStore` abstraction (JSON by default, org-scoped DB
behind `APP_PROJECTS_BACKEND`), and a request→org resolver. We'll be honest —
the *cutover* that actually enforces org isolation on every route is staged, not
flipped. But the seam is in, so "both deployment models" isn't a future rewrite.

## Polyglot, for real this time

Python and TypeScript were genuinely end-to-end. Java was a wedge. v0.9.0 wires
**JaCoCo** into the Evaluator's coverage signal with format-aware parsing, so the
Java lane's coverage-delta actually computes instead of erroring on the wrong
parser. C# and Go are on the roadmap, not in the box — and we'll say so.

## Plumbing that unblocks adoption

Two smaller things that matter for real deployments: **per-user `acw_` API
tokens** with an `api:full` scope, so the handover skill and CLI authenticate as
*you* instead of sharing one host-wide secret; and verified **Ollama Cloud**
support via the OpenAI-compatible path, for teams that want a capable model with
their own egress posture.

## A forward bet: WebMCP

One thing we're *planning*, not shipping. [WebMCP](https://www.w3.org/community/webmachinelearning/)
is an early W3C Community-Group draft — `navigator.modelContext.registerTool()` —
that lets a web page expose typed, callable tools to an in-browser agent. It's
Chrome-Canary-only today, so treat this as a forward bet. But a page's agent
tools are a new public surface nobody verifies, and that is squarely our lane:
bring the verdict pipeline to it, and expose TFactory's own actions as WebMCP
tools so an agent can drive the portal from the open tab. Epic's scoped,
default-off, experimental.

## The honest ledger

Shipped (0.9.0, with a 0.9.1 hotfix and 0.9.2 in flight): the PR gate, generic
ingestion, issue→native-task, tenant groundwork, Java coverage, per-user tokens,
Ollama Cloud. Planned or experimental: the org-scoping cutover, C#/Go, WebMCP.
We'd rather tell you which is which than blur the line.

Next: [how TFactory decides a test is worth keeping](/architecture/).
