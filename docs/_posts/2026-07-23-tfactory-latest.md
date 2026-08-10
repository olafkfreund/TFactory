---
layout: post
title: "The verifier got faster, safer, and harder to fool"
subtitle: "A batch of releases tightened the Nix verify lane, isolated every concurrent build, made reaped Jobs finish honestly, and closed an SSRF hole in the health check. Here is what changed and why it matters."
date: 2026-07-23 12:00:00 +0000
author: DataSeek Team
---

TFactory verifies finished features: it takes a signed acceptance contract and a
deployed URL, generates tests aligned to the declared criteria, runs them in a
reproducible sandbox, and returns a verdict you can trust. Over the last few
weeks the verifier itself was under test. A run of releases made it faster, made
concurrent runs safe from one another, made failure honest, and closed a real
security hole — the difference between a verdict you can lean on and one you
cannot.

## The Nix lane stopped paying the toolchain tax twice

Every verify runs inside an ephemeral Kubernetes Job on a per-task Nix
toolchain, so the environment is reproducible down to the store path. The
problem: the evaluator dispatches one Job per test, per stability re-run, per
mutation candidate — `S x (3 + M)` Jobs for a single spec — and each Job used to
re-realise the identical Python closure (python, pytest, gcc-wrapper, stdenv)
from `cache.nixos.org`. One twelve-test spec blew the 3600-second verify
deadline before producing a single verdict, while the same spec on the host
runner finished in ten minutes.

The fix bakes the common closure into the runner image at build time: a warm-up
flake carrying `python313.withPackages [pytest pytest-cov pip]` is realised and
gc-rooted during the image build, so a per-task flake resolving to those same
paths finds them present and skips the fetch. A pinned `flake.lock` ships with
the image so Jobs stop re-locking nixpkgs, and stability samples now batch into
one Job per subtask. When a real application declares its dependencies in
`requirements.txt`, the lane pip-installs them per Job rather than leaning on a
curated allowlist that could never be complete — while a hermetic repo still
gets a byte-identical environment, so nothing reproducible regressed.

## Concurrent verifies stopped corrupting each other

When several specs verify at once, they used to share one git clone of the build
checkout — and a checkout of spec B's branch could land under spec A's feet.
Each spec now gets its **own git worktree** (#742), isolated from its siblings.
A rerun resolves the spec's own worktree rather than the shared clone, terminal
specs have their worktrees garbage-collected at startup to reclaim PVC disk, and
a build branch that cannot be checked out now fails loudly instead of silently
testing the wrong tree.

## A killed Job now finishes its spec

The reaper that cleans up Jobs past their deadline used to mark an internal
state row `stuck` and stop there. But every reader that asks "is this spec
done?" reads `status.json` — so a reaped Job left the workspace reading
`evaluating` forever, indistinguishable from still-working. The reap now writes
a real terminal verdict (`status=failed`, `phase=verify_job_reaped`) into the
workspace, while a spec that already reached its own verdict is left untouched.
Inline stages that stall go terminal too, and stranded specs are reconciled on
control-plane startup, so a pod roll no longer leaves work in limbo.

## The health check stopped being an SSRF vector

Before a browser or API lane runs, TFactory health-checks the target. That check
took a URL and fetched it — which meant a malicious or careless target URL could
point at cloud-instance metadata (`169.254.169.254`) or a link-local address and
have the verifier fetch it from inside the cluster. The MCP health check now
blocks cloud-metadata and link-local ranges outright. It is a small guard on a
path that most people never think about, which is exactly why it mattered.

## Verdicts you cannot fool, at a level you can read

Two properties underpin all of this. First, verdicts are **mutation-checked**: a
guard that still passes after its own logic is mutated is not a real guard, and a
test that survives the mutation does not ship. Second, every verdict carries a
**Verification Assurance Level** that states how hard reality pushed back —
VAL-0/1 for sandboxed tests, VAL-2 for a deployment validated with `kubectl
apply --dry-run=server` against the detected manifests, VAL-3 for a real
disposable target. The VAL travels with the verdict into the PR triage report
and the CFactory cockpit, so a green check never claims more assurance than it
earned.

Every environment variable that drives this — the Nix runner knobs, the verify
backend selector, the side-effect gates — is now documented end to end in the
[environment reference](/environment-reference/). The verifier is boring on
purpose. Boring is what you trust at 3am.
