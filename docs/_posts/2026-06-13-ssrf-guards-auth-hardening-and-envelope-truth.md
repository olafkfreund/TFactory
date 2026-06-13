---
layout: post
title: "SSRF guards, auth that fails closed, and one version that can't lie"
subtitle: "An audit found three ways the verifier could be turned against the things it verifies — a metadata-reachable test lane, an unauthenticated network bind, and a schema version that could drift. This cycle closes all three, additively, behind the required backend gate."
date: 2026-06-13 09:00:00 +0000
author: Olaf Freund
---

TFactory is the **Reflect** stage of the Factory line: it takes a finished branch
from AIFactory, generates tests, runs them, and emits a completion event the rest
of the line trusts. That trust cuts both ways. A verifier that fetches URLs handed
to it, binds a control plane to the network, and stamps a schema version onto every
result is, if you squint, three attack surfaces wearing a lab coat. A deep audit
this week squinted, and found all three. This cycle closes them.

None of the fixes change a happy path. They change what happens when the input is
hostile, the host is wrong, or two numbers disagree — the cases that decide whether
a system is safe or merely lucky.

## A test lane will fetch whatever URL you hand it (SSRF)

The browser, api, and integration lanes take their target URL from the AIFactory
handoff — the deployed app's address — and feed it to a `urllib` health-poll and
into the test container as `TFACTORY_TARGET_URL`. That URL is, by construction,
attacker-influenceable: it rides in on a handoff. Without a guard, a crafted handoff
could point a lane at `169.254.169.254` — the cloud-metadata endpoint on AWS, GCP,
and Azure — and the lane would dutifully fetch it, handing back instance credentials
or reaching an internal service. That is a textbook SSRF, and on a runner with an
IAM role attached it is a credential-exfiltration path.

`#361` adds `net_guard` (`apps/backend/tools/runners/net_guard.py`), a stdlib-only
module with one job: resolve the target host and refuse it if it lands in a dangerous
range. The decision table is deliberately blunt:

- **Always blocked, no override:** link-local / cloud-metadata (`169.254.0.0/16`),
  IPv6 link-local (`fe80::/10`), and IPv6 unique-local (`fc00::/7`, which covers the
  `fd00::/8` ULA space). These are never a legitimate test target, so there is no flag
  to allow them. That is the core SSRF defence.
- **Blocked unless `allow_loopback=True`:** `127.0.0.0/8` and `::1`. AppRuntime's
  docker-compose health-poll legitimately targets localhost, so it opts in explicitly;
  the untrusted handoff URL does not.
- **Blocked unless `allow_private=True`:** RFC-1918 (`10/8`, `172.16/12`, `192.168/16`),
  for the case where an operator vouches for a same-cluster integration target.

The guard resolves hostnames through DNS and checks *every* address returned, so a
name that mixes a public and an internal answer (a classic DNS-rebinding move) still
trips it. It runs before any fetch on the network-enabled lanes, and it is
dependency-free so it imports cleanly in both the runner containers and the backend
test venv.

The design decision worth calling out: metadata and link-local are blocked
*unconditionally*, while the local compose app is allowed via an explicit opt-in. The
guard is permissive exactly where the system already trusts itself (loopback) and
absolute where it must never reach (the metadata endpoint), with private ranges in
the middle gated behind an operator's say-so. There is no global "off" switch for the
dangerous ranges, because the whole point is that a hostile input cannot flip one.

## Disabling auth should be loud, not load-bearing

`DISABLE_AUTH=true` injects a default admin into every request. It is a real
dev-time convenience and it stays. The danger is the combination: `DISABLE_AUTH=true`
*and* a non-loopback `HOST` (say `0.0.0.0`). That quietly exposes an unauthenticated
control plane to the network — the kind of thing that is fine on a laptop and
catastrophic on a box with a public interface, and nothing stopped you crossing that
line.

`#361` adds a startup guard (`apps/web-server/server/config.py`) that **fails closed**:
the web-server refuses to boot if `DISABLE_AUTH` is true while `HOST` is not loopback.
It does not warn and continue — it raises and the process never comes up. The default
is the safe one; you have to set an explicit escape-hatch env var to bind an
unauthenticated server to the network on purpose.

That hard-fail immediately broke CI, because the pytest suite boots the real app with
`DISABLE_AUTH` on `0.0.0.0` inside an isolated, trusted runner — exactly the shape the
guard is built to reject. The wrong fix would have been to soften the guard. `#362`
does the right thing instead: it exempts the test run specifically, keying off
`PYTEST_CURRENT_TEST`, an environment variable Python's pytest sets only while a test
is actually running and which is never present in a real deployment. The guard still
protects production unchanged; it just recognises that the CI sandbox's network bind
is a deliberate, trusted one. Fail-closed everywhere real, with a single narrow,
provable exemption for the place we control.

While in the dispatch path, `#361` also closed a GitHub Actions script-injection in
`tfactory-dispatch.yml`. The untrusted issue *title* was being interpolated straight
into a `curl` JSON body — a `$(...)` or backtick in a title would execute in the
runner. The fix is the standard one: the title moves into an `env:` block
(`ISSUE_TITLE`) and is referenced as a shell variable, so GitHub's expansion never
splices attacker text into the command line.

## One version, parsed from the contract, that cannot drift

The completion envelope TFactory emits carries a `schema_version`. That version lived
in two places that were *required* to agree but had no mechanism forcing them to: the
vendored JSON schema's `$id` (the published contract CFactory and the sibling
factories validate against), and a Python literal the Triager stamped onto every
event. Bump one without the other and CFactory sees a `schema_version` that
contradicts the `$id` it just validated — and the disagreement is silent. Nobody gets
an error; the line just quietly disagrees with itself.

`#363` (tracked under `#360`) deletes the second number. `completion_schema.py`
now makes the **JSON schema the single source of truth**: the version is parsed from
the schema's `$id` at import time, and both `apps/backend` (the Triager, which
produces events) and `apps/web-server` (the relay, via the shared `agents` package)
read that one constant. There is no Python literal left to forget — you change the
schema's `$id` and the code follows. A test asserts the `$id`, the schema `title`,
and the runtime constant all report the same version, so the drift we were guarding
against is now structurally impossible rather than merely discouraged.

Riding alongside, `#364` began decomposing the `routes/tasks.py` god-file, lifting the
IDE/terminal launcher routes into their own `worktree_tools.py`. No behaviour change —
just paying down the kind of file that hides bugs by being too big to read.

## The seam gate, re-engineered

One program-level note. The Factory line runs a post-deploy PARR seam-gate that smoke-
tests the boundaries between services after a deploy. The first cut wired it as a
cross-repo *reusable workflow*, which turned out to fail the Deploy workflow at
*startup* — the reusable-workflow form broke before it could run anything. That was
reverted (`#366`), then re-landed as a steps-based, soft gate inside the deploy job
(`#367`). Same intent — catch a broken seam the moment it ships — without the form
that took the whole workflow down with it.

## Why all of this is additive

Every change here is additive and merged behind the required backend gate
(`ruff + pytest`, plus the fast `critical` lane). The SSRF guard only ever *adds* a
refusal on inputs that were already unsafe; the auth guard only refuses a
configuration that was already dangerous; the envelope change removes a number without
changing the value it reported. A green build before this cycle is still green after
it. The point of a Reflect stage is to be the part of the system you can trust to tell
you the truth — these three fixes are about making sure it can't be quietly turned
into something that lies.
