---
layout: default
title: Demo & Showcase
permalink: /showcase/
nav_order: 5.5
---

# v0.2.0 in action — a live demo

<div class="reveal" markdown="1">

This page is the receipt. We took TFactory v0.2.0, handed it a small live
SUT (a Vite + React greeting generator with 5 acceptance criteria and a
deliberately seeded cache bug), and let the four-agent pipeline plan,
write, sandbox, score, and triage a Playwright suite end-to-end. Below
you'll find the time-lapse of the portal, the generated tests, the
captured evidence (screenshots, video, trace, HAR), and the unedited
triage report the Triager produced — exactly what a reviewer would see
on the AIFactory PR.

</div>

## The demo app

<div class="reveal" markdown="1">

- **Live SPA:** [olafkfreund.github.io/tfactory-demo/](https://olafkfreund.github.io/tfactory-demo/)
- **Source:** [github.com/olafkfreund/tfactory-demo](https://github.com/olafkfreund/tfactory-demo)

The system-under-test is intentionally tiny: a Vite + React single-page
app with two dropdowns (tone, audience), a **Generate** button, a
**Clear** button, and an output panel. The 5-AC surface covers happy
path, every dropdown combination, the Clear reset, accessibility on the
buttons, and a cache-warmup behaviour. AC#5 is the failing case — the
SUT ships with a seeded cache bug so the pipeline has something real to
flag.

</div>

## The user story

<div class="reveal" markdown="1">

> **As a** marketer drafting outreach copy,
> **I want to** generate short greetings with selectable tone and audience,
> **so that** I can prototype message variants without leaving the browser.

**Acceptance criteria** — exactly what TFactory was handed:

1. **AC#1 — Happy path.** With default selections, clicking *Generate*
   produces a non-empty greeting in the output panel within 1s.
2. **AC#2 — Tone × Audience matrix.** Every (tone, audience) combination
   produces a greeting consistent with the selected tone keyword.
3. **AC#3 — Clear resets.** Clicking *Clear* empties the output panel
   and re-enables *Generate* without a page reload.
4. **AC#4 — Buttons are accessible.** *Generate* and *Clear* expose
   `aria-label`s, are keyboard-focusable, and survive a Tab traversal in
   document order.
5. **AC#5 — Cache warmup is observable** *(expected to fail — the SUT
   has a seeded cache bug)*. The second *Generate* call for an identical
   (tone, audience) pair should hit the in-memory cache and complete in
   under 50ms; the bug makes it bypass the cache and re-compute.

</div>

## The pipeline running

<div class="reveal" markdown="1">

The portal's `LaneStatusGrid` lights up phase-by-phase as the Planner →
Gen-Functional → Executor → Evaluator → Triager pipeline runs end-to-end
against the demo app. The time-lapse below was captured from
`http://localhost:3110` during a real run — no edits, no faked states.

</div>

<figure class="reveal" markdown="1">

![Portal phases during the pipeline run]({{ SHOWCASE_GIF_URL }})

*The TFactory portal's LaneStatusGrid lighting up phase-by-phase
as the Planner → Gen-Functional → Executor → Evaluator → Triager
pipeline runs end-to-end against the demo app.*

</figure>

[Watch the full MP4 →]({{ SHOWCASE_MP4_URL }})

## What got generated

<div class="reveal" markdown="1">

The Gen-Functional agent emitted `{{ SHOWCASE_GENERATED_TEST_COUNT }}`
Playwright `.spec.ts` files into the demo repo, one per acceptance
criterion. They landed on a feature branch via the Triager's
`git_writer` (run in **write mode** for the demo, opt-in via
`TFACTORY_TRIAGER_GIT_WRITE=1`):

→ [See the generated tests on the demo PR]({{ SHOWCASE_GENERATED_TESTS_URL }})

The files:

{{ SHOWCASE_GENERATED_TESTS_LIST }}

Each file imports only from the demo's existing test harness — pre-flight
static checks confirmed every `import` resolved before the test was kept,
and the flake-risk linter cleared each file of dict-iteration order,
`time.sleep`, and unfrozen `datetime.now()` patterns.

</div>

## The test evidence

<div class="reveal" markdown="1">

Per Decision 11 in the v0.2 design, browser-lane tests don't report line
coverage (the test drives the browser, not the framework code) — instead
they ship **screenshots, video, trace.zip, and network HAR** as
verification evidence, captured automatically by the
`tfactory-runner-playwright` image.

</div>

### AC#5 video — the failing case

<div class="reveal" markdown="1">

This is the most visual artefact: the recording of AC#5 watching the
second *Generate* call miss the cache, recompute, and overshoot the
50ms budget.

<video controls preload="metadata" style="max-width: 100%; border-radius: 6px;">
  <source src="{{ SHOWCASE_AC5_VIDEO_URL }}" type="video/mp4">
  Your browser does not support embedded video.
  [Download the MP4]({{ SHOWCASE_AC5_VIDEO_URL }}) instead.
</video>

</div>

### Screenshot thumbnails

<div class="reveal" markdown="1">

One screenshot per acceptance criterion, captured at the moment of
assertion failure (or at the final passing state for AC#1-4):

{{ SHOWCASE_SCREENSHOTS_THUMBNAIL_STRIP }}

</div>

### Downloads

<div class="reveal" markdown="1">

The Triager attaches the full evidence bundle as PR-comment-linked
downloads — re-runnable locally with `npx playwright show-trace`:

{{ SHOWCASE_EVIDENCE_DOWNLOAD_LIST }}

</div>

## The verdict (what humans see)

<div class="reveal" markdown="1">

Below is the **unedited** `findings/triage_report.md` the Triager
produced for this run. Same Markdown the reviewer sees when they open
the file in their PR diff, and the same body the Triager posts as a PR
comment when `TFACTORY_TRIAGER_PR_COMMENT=1` is set.

</div>

<div class="reveal" markdown="1">

{{ SHOWCASE_TRIAGE_REPORT_INLINE }}

</div>

## Reproduce it yourself

<div class="reveal" markdown="1">

Everything below runs against a fresh checkout of TFactory v0.2.0 plus
the public demo repo — no private state, no hidden flags. Expected
end-to-end time on a developer laptop: ~6-8 minutes.

1. **Clone both repos.**
   ```bash
   git clone https://github.com/olafkfreund/TFactory
   git clone https://github.com/olafkfreund/tfactory-demo
   ```

2. **Install dependencies.**
   ```bash
   cd TFactory
   npm run install:all
   cd apps/web-server && uv venv && uv pip install -r requirements.txt
   ```

3. **Authenticate Claude Code** (one-off; uses your subscription, no API
   key needed):
   ```bash
   claude setup-token
   ```

4. **Start the backend with auto-fire enabled** so the pipeline chains
   Planner → Gen-Functional → Executor → Evaluator → Triager without
   manual clicks:
   ```bash
   TFACTORY_AUTO_PLAN=1 \
   TFACTORY_AUTO_GENERATE=1 \
   TFACTORY_AUTO_EVALUATE=1 \
   TFACTORY_AUTO_TRIAGE=1 \
     python -m server.main
   ```

5. **Seed the AIFactory-style workspace** for the demo SUT (snapshots
   the spec + diff + source.json the Planner reads):
   ```bash
   ./scripts/seed-aifactory-workspace.sh
   ```

6. **Open the portal** at [http://localhost:3110](http://localhost:3110),
   click **New Task**, pick the `tfactory-demo` project and spec
   `001-greeting-generator`.

7. **Wait for `status=triaged`** in the LaneStatusGrid (~6-8 min). The
   portal will surface each phase transition live; the gif at the top of
   this page is a time-lapse of exactly this wait.

8. **Open the triage report** at
   `~/.tfactory/workspaces/<project_id>/specs/001-greeting-generator/findings/triage_report.md`
   — that's the same file inlined above.

</div>

<div class="reveal" markdown="1">

→ [v0.2.0 release notes](/CHANGELOG/#v020--enterprise-test-framework-spine-2026-05-29) &nbsp; · &nbsp; → [Status by lane](/#status-by-lane)

</div>
