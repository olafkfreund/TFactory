---
layout: post
title: "A verdict you can take to an auditor"
subtitle: "Three weeks on verification: rejecting a generated test that claims it corrected the spec, calling a contradictory acceptance criterion UNVERIFIABLE instead of passing it, real assurance levels after a defaulting bug graded every lane as unit, and a deploy dry-run that proves the change would apply."
date: 2026-08-10 09:00:00 +0000
author: DataSeek Team
---

TFactory is the half of the Factory that is supposed to be unconvinced. It takes
what the builder produced and tries to establish whether it does what was asked,
using tests that actually run. The value of that role is entirely in its
scepticism, so most of the last three weeks went on the ways a verifier can fool
itself.

## A test that rewrites the spec is not a passing test

The sharpest change: a generated test whose own prose claims it **corrected** the
specification is now rejected outright.

This happens more than it should. A coding agent writes a test, finds the
behaviour does not match the acceptance criterion, and resolves the conflict by
adjusting the test and noting — in a comment or a docstring — that the criterion
was wrong. The suite goes green. The acceptance criterion is now decorative, and
the green is a measurement of the test's agreement with the code rather than with
the requirement.

A related change: an acceptance criterion that contradicts itself is now reported
as **UNVERIFIABLE** rather than being quietly passed or failed. Neither answer is
honest when the question is incoherent, and inventing one destroys the
information that the criterion needs fixing.

## Assurance levels that mean something

Verification Assurance Levels grade a run by what kind of evidence it produced —
unit, integration, API, browser. For a period, every run capped at VAL-0 and
nobody could see why.

The cause was a default. The grader grouped verdicts by their lane and treated a
missing lane as `unit`. Nothing ever wrote that field. So API, browser and
integration verdicts were all being graded as unit, VAL-2 saw none of them, and
the ceiling was structural rather than earned.

Two fixes: the evaluator now stamps each verdict's lane from the test plan, and a
verdict with no lane is **excluded rather than defaulted** — unattributed evidence
is not evidence about a lane. The first run afterwards reached VAL-2 against a
VAL-2 target with the gate finding nothing to correct.

That was not the end of it, which is the useful part. The same run's
acceptance-criteria fidelity report said "verified 2 of 6, flagged-only 3", while
the VAL calculation counted a flag as a pass. Two honest components disagreeing
produces a dishonest headline, so the VAL claim now names the flag count and
cannot read as a clean pass.

## Things that reported success and had not

Several fixes this month were the same defect in different clothes: a status
channel describing the process rather than the artefact.

- **A Job that exited zero had handed off, not died.** The reaper inferred death
  from `active == 0`, which conflates "succeeded" with "was killed". A Job that
  finished its stage and dispatched the next one had its whole spec marked failed
  — the mechanism behind a run of false failure anomalies on the board.
- **Coverage paths were dead on arrival.** Both runner branches built the JUnit
  and coverage paths inside a scratch directory and deleted it in a `finally` that
  runs before the result reaches the caller. The file existed at construction and
  never again. Artefacts are now persisted before the scratch directory dies.
- **A skipped equivalence lane was recorded only in the log**, so the findings
  file — the thing downstream reads — showed no trace of a lane that had not run.
- **Project coverage was being averaged**, which is wrong: coverage across a
  project is the union of covered lines, not the mean of per-file percentages.
- **The sandbox flag never reached the Job.** The verify Job builds a curated
  environment allowlist, and the flag lived only on the control-plane deployment,
  so the agent inside re-enabled its sandbox and the pod's own seccomp profile
  denied it. Every agent shell call failed. That was the third time the allowlist
  trap has cost us, which is why it is now written down rather than remembered.

## Verification that includes the deploy

Verification used to stop at the tests. It now includes a **deploy lane** that
runs `kubectl apply --dry-run=server` against the real API server, so a change
that passes its tests but produces a manifest the cluster would reject is caught
before merge rather than after. That lane counts toward VAL-2.

It is deliberately dry-run only, and the guard for that lives in the runner
rather than in the trigger — so widening which changes get a deploy proof can
never make an apply effectful. That separation was checked, not assumed, when the
trigger was widened to medium-risk changes this month.

## Supply chain and the boring infrastructure

The runner images are signed with keyless signing, pinned by digest, published
with parseable tags, and labelled with the repository they were built from. The
signing job declares its identity token at the job rather than the workflow, so
the credential is not handed to every step that happens to run alongside it.

A subtle one worth recording: CI was pinned to a runner image that the pipeline
did not build. Everything was green, and the thing being tested was not the thing
being published.

## Tracing from inside the Job

Verification runs in a Kubernetes Job, which is a new process with no memory of
the run that dispatched it. The run's trace context is now carried across that
boundary and emitted from inside the Job, so a verification appears in the trace
as work rather than as a gap between two spans. As everywhere else in the fleet
this month, it counts as working only when a span lands.

## Where this leaves us

The honest summary is that TFactory got harder to satisfy. A verdict now has to
survive a test that cannot quietly rewrite the requirement, a lane attribution
that will not default in its own favour, an artefact check rather than an exit
code, and a deploy that would actually apply.

That is a slower pipeline and a verdict worth having.
