---
layout: post
title: "The verify Job went live, and now regression never sleeps"
subtitle: "Two weeks ago we said the Job-native verify executor was close but the production default flip was not live, and we said exactly why. This week it flipped. Here is what that unlocked: every verdict now comes from a per-task Kubernetes Job in a contract-pinned Nix toolchain, and a regression suite re-runs the persisted test corpus over that same substrate without anyone re-triggering it."
date: 2026-06-23 12:00:00
author: DataSeek Team
---

A platform earns trust by closing its own open loops in public. On 2026-06-21
we wrote that the Job-native verify executor was *close* but that the production
default was still the in-pod path, and we listed the blockers by number. This
post is the other end of that sentence: the flip is live, and on top of it sits
a regression suite that re-verifies a project's whole test corpus on a schedule.

## The loop we left open

The honest line from a fortnight ago: TFactory could run the verify pipeline as
a per-task Kubernetes Job in a contract-declared Nix toolchain, but the
**production default remained the in-pod path**. The default flip was gated and
had been reverted, pending a clean re-validation. We named the three things that
had to land first:

- The Job had to run on the TFactory image so the agents import (#479).
- The Job environment had to receive LLM provider credentials (#480).
- File-auth CLI credentials had to reach the Job without ending up on a command
  line (#481) — seeded by an initContainer, never argv.

All three are in. With them, the flip itself (#466) is closed and the reference
deployment now sets `TFACTORY_VERIFY_EXEC=kubejob`.

## What "live" actually means

It means a verdict is now produced by a real, short-lived Job — not a subprocess
of the long-running web pod. We proved it the only way that counts: a
`dispatch_verify_job` smoke run against the live cluster.

- The evaluator ran inside the Job and **executed the generated pytest against
  the real code** — not a static read of the test file. Stability came back 3/3
  across repeats; a mutation check killed the planted mutant.
- The triager ran an **in-Job LLM session** to classify the result, not a
  pre-baked stub.
- The pipeline returned a verdict of `accept`, and — the part that matters for a
  crash-safe platform — it wrote a **durable `done`/`triaged` job-state row** and
  a traceability matrix, so the verdict survives a pod restart and can be audited
  later.

The in-pod path has not been deleted. It remains the shipped *code* default and
the safe fallback for a laptop install. "Live" here is precise: it is the
*deployment* default on the reference cluster, set in gitops — not a default
baked into the binary. A fresh install with no flags still runs in-pod until an
operator opts in.

## The thing the flip unlocked: regression that runs itself

Running verification once is table stakes. The interesting failure is the test
that was green on `main` last week and is red today because something *else*
changed — and nobody thought to re-run verification. That is what the new
[regression suite](/regression-suite/) (RFC-0018) is for, and it is built
directly on the now-live Job substrate.

A run does four things:

1. **Loads the corpus** — the persistent `.tfactory/tests-catalog.json`, the
   cross-run record of every test the platform has generated and verified for a
   project.
2. **Executes** — each test runs on the same Nix-flake-per-task Kubernetes Job
   path the verify flip just made the default. The toolchain comes from the
   contract's `environment` manifest, so the regression environment matches the
   build and verify environments with no drift. The runner refuses to silently
   fall back to an in-pod host venv.
3. **Diffs against a baseline** — every test is classified: a `regression`
   (passed in the baseline, fails now) fails the gate; a `fix` (failed before,
   passes now) advances the baseline; flaky tests are quarantined out of the gate
   on their own history.
4. **Publishes a read-model** — the latest verdict, current regressions and
   fixes, the quarantine list, and run history, surfaced on the task-detail
   **Regression** tab in the portal.

And it runs without anyone asking. The same engine is reachable five ways: a CLI
(`python -m agents.regression run`, exit code 1 on any regression so CI can gate
on it), a default-off **nightly CronJob** in the Helm chart, an on-demand HTTP
endpoint (`POST /api/projects/{id}/regression/run`), an MCP `regression_run`
tool, and the portal. The unattended path — the one that catches the silent
breakage — is the nightly Job.

## Why these two land together

They are the same idea at two time scales. The verify flip makes a *single*
verdict come from a reproducible, crash-durable Job in a pinned toolchain. The
regression suite takes that exact guarantee and applies it *continuously* to a
project's whole history, so "green" keeps meaning green after the code that the
tests cover moves on. One without the other is half a promise.

## The honest line, again

Live and proven on the reference deployment: Job-native verify is the deployment
default, regression runs on the same substrate, and both write durable state. The
in-pod path stays as the shipped code default and the fallback. We will keep
marking that distinction precisely, because "the deployment runs it" and "the
binary defaults to it" are different claims, and only one of them is true today.

The loop we opened two weeks ago is closed. The next one — regression coverage
across more languages and lanes — is already open, and we will write that line
when it lands too.
