---
layout: post
title: "Verdicts you can trust: VAL ladders, real pytest runs, and an honest verify-Job rollout"
subtitle: "This week TFactory shipped the Verification Assurance Level ladder end to end, made the host-venv pytest fallback the thing that actually runs tests on real code, and got the Job-native verify executor close — but the production default flip is not live yet, and this post says exactly why."
date: 2026-06-21 12:00:00
author: DataSeek Team
---

A test platform is only as good as the honesty of its verdict. This week's
TFactory work is about three things that make the verdict mean something: a
**Verification Assurance Level (VAL)** ladder that says how far a result was
actually verified, a pytest path that genuinely **runs the generated tests
against the real code**, and the in-progress move to executing the whole verify
pipeline as a **per-task Kubernetes Job**. Two of those are shipped. The third
is close, and this post is explicit about the gap.

## The VAL ladder: how far did we really get? (shipped)

A green checkmark collapses a lot of nuance. "Tests passed" can mean "we ran the
suite against the deployed app and every acceptance criterion held" or it can
mean "we ran some unit tests in a sandbox and never touched a live target."
RFC-0006 replaces the single boolean with a **Verification Assurance Level**: a
ladder from VAL-0 (the suite executed) up through lanes that hit the real API,
integration surface, and browser, each level only claimable when the evidence
for it exists.

The Report tab on a finished task now leads with the VAL line. Here is a real
run reading `Verified to VAL-0. NOT verified: VAL-1 failed; VAL-2 not_run (no
api/integration/browser lane ran in this verify); VAL-3 not_run`:

![A TFactory verify report — the VAL ladder states exactly how far the run was verified, above the triage buckets]({{ '/static/img/gallery/09-task-report.png' | relative_url }})

That is the point. The run does not pretend it reached a level it did not. The
VAL gate keeps the result honest: a lane that could not run is `not_run`, not a
silent pass, and the triage buckets below it (here, two `Rejected`) are scoped to
what was actually exercised. The traceability matrix shipped alongside it
(RFC-0015 D2) maps each requirement to the test and the VAL it earned.

## Acceptance fidelity, scoped to VAL (shipped)

The Acceptance tab is the same discipline applied per criterion. It grades each
acceptance criterion `verified` only when a test that exercises it actually
passed — and labels the rest `UNVERIFIED` rather than rounding up:

![The Acceptance tab — verified 0/6, every criterion labelled UNVERIFIED with the test that rejected it]({{ '/static/img/gallery/10-task-acceptance.png' | relative_url }})

"Verified 0/6 acceptance criteria (flagged-only: 0, unverified: 6). NOTE: not
every acceptance criterion is verified by an accepted test." That sentence is the
product. A reviewer reading this knows precisely what the suite proved and what
it did not — and the per-criterion `reject` links point straight at the verdict
that explains why.

## The host-venv pytest fallback: tests that actually run (shipped)

Generating good tests is worthless if they never execute against the real code.
We hit exactly that failure mode earlier: in a k3d pod with no container runtime,
the pytest lane could not launch its runner and would `ModuleNotFoundError:
pytest` instead of running anything — a suite that looked written but never ran.

The fix is a **host-venv fallback**: when there is no container runtime, the
Evaluator stages the system-under-test into a scratch worktree and runs pytest in
a host virtualenv, collecting JUnit XML and coverage back the same way the
sandboxed runner would. The verdict comes from a real execution against the real
code — the Verdicts tab below is `bench-go-hello` with two genuine `reject`
verdicts, each carrying its coverage, stability, mutation, lint and semantic
signals:

![The Verdicts tab — two real reject verdicts with the five-signal breakdown and a merge-preview header]({{ '/static/img/gallery/08-task-verdicts.png' | relative_url }})

## Job-native verify execution: close, but the default flip is NOT live

The bigger structural change is RFC-0017's move to running the **entire verify
pipeline as a per-task Kubernetes Job** (extending RFC-0016's Job-native model),
so the verify environment is a per-task Nix toolchain that matches the build env
with no drift, instead of running inside the long-lived web pod.

The mechanism landed this week, and the supporting fixes that got it close are
merged:

- **#479** — the verify-orchestration Job now runs on the TFactory image, so it
  has the agents it needs to import and run (an earlier image was missing the
  package and the Job failed at startup).
- **#480** — the Job env now receives the LLM provider credentials, so the
  agents inside the Job can call their model (with the env-var name inlined in the
  no-credential log to clear a CodeQL false positive, not the secret).
- Earlier in the chain: durable Postgres job-state for verify tasks, a warm
  `/nix-store` PVC mounted into the Nix lane Jobs, and wiring Job-native dispatch
  into the pipeline.

**Here is the honest part.** The production default is *still the safe in-pod
path*. The commit that made nixjob the default verify execution path
(`#466` / `#469`) is gated behind `TFACTORY_NIX_RUNNER_IMAGE` and a
contract-declared Nix env, and it **falls back to the in-pod host/docker runner**
whenever that configuration is absent or unavailable — which is the case in the
live deployment today. The default flip was **reverted pending re-validation**:
Job-native build validation did not pass cleanly, and we do not flip a default in
production on a path we have not re-proven. `#479` and `#480` are the fixes that
brought it within reach; the flip itself waits on a green re-validation. Until
then, every verify you see on the live portal ran on the in-pod path.

You can see that in the live board itself. The RFC-0017 staging tasks are sitting
in `planning` and one in `failed` (replan budget exhausted) on the in-pod path —
not green Job-native runs:

![The live TFactory pipeline board — RFC-0017 staging tasks in planning and failed on the in-pod path]({{ '/static/img/gallery/02-home-projects.png' | relative_url }})

That `failed` task is not a bug in the post — it is the truth of where the
rollout is.

## Browser-screenshot verify lane

The browser lane (the source of screenshot and recording evidence) runs in its
own per-task Nix toolchain inside an ephemeral Kubernetes Job and is the path
that produces visible evidence. When a task has no browser lane — like the Go
binary above — the Evidence tab says so plainly rather than inventing pictures:
"No screenshots or recordings captured — the browser lane produces these when it
runs." Honest absence beats a fake gallery.

## What changed, in one breath

The verdict now states **how far** it was verified (VAL ladder), grades **each
acceptance criterion** against a test that actually ran, and is backed by a
**real pytest execution** even where there is no container runtime. The
Job-native verify executor is wired and its blocking fixes are merged — but the
production default is still the in-pod path, and it stays that way until the
Job-native run re-validates green. That last sentence is the difference between a
roadmap and a receipt.
