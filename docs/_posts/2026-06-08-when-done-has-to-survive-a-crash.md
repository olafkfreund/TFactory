---
layout: post
title: "When \"done\" has to survive a crash"
subtitle: "An event you only send once isn't a contract — it's a hope. v0.7.0 makes TFactory's completion handoff durable, idempotent, traceable, and bounded, then fixes the login that was quietly bouncing everyone back to the door."
date: 2026-06-08 12:00:00
author: DataSeek Team
---

Last cycle TFactory [stopped working alone]({% post_url 2026-06-05-tfactory-stops-working-alone %})
— it started picking up governed work from PFactory and announcing completion on
a schema the whole Factory line could read. That was the happy path. This cycle
we asked the unglamorous question that decides whether a distributed system is
real or a demo: **what happens when the happy path doesn't happen?**

v0.7.0 is the answer, across four fronts.

## A completion event you send once is a hope, not a contract

TFactory is the **Reflect** stage of the line: it verifies a feature and emits a
completion event so [CFactory](https://cfactory.freundcloud.com/) can thread it
into a WorkItem. The old emitter was fire-and-forget — one POST, best effort. If
the process crashed *after* the terminal `triaged` write but *before* the POST
landed, the event was simply gone. CFactory's WorkItem sat stale forever, and
nothing knew to retry.

That's not a bug you hit often. It's a bug you hit at the worst possible time —
under load, mid-deploy, exactly when you most need the line to be honest about
what finished.

So completion delivery is now **at-least-once** (#281). Because TFactory's
pipeline is file-based — the terminal state change is a `status.json` write, not
a database transaction — the outbox is a durable directory of atomically-written
JSON entries. The Triager enqueues the envelope *before* attempting delivery, so
once that write returns the event survives a crash. A relay drains the outbox
with exponential backoff, deletes each entry on a `2xx`, dead-letters after a cap
so it never spins forever, and replays anything undelivered across restarts. The
web-server runs the relay on a timer; an operator can also drain it by hand.

## At-least-once needs an identity to dedup on

"At least once" is honest, but it means a consumer can see the same event twice.
For that to be safe, every event needs a stable identity. So the envelope grew —
**additively** (#282), nothing removed: a per-event UUID `id`, CloudEvents-core
fields (`source`, `type`, `time`, `specversion`), and a W3C `traceparent` that
inherits an upstream trace so a single request can be followed across PFactory →
AIFactory → TFactory → CFactory.

The `id` is the keystone. It's generated once when the envelope is built, stored
in the outbox, and re-sent verbatim on every retry — so it rides on the wire as
the `Idempotency-Key`. At-least-once delivery plus a stable id equals
effectively-once downstream. The two changes only make sense together, which is
why they shipped together.

## A correction loop that knows when to give up

When TFactory's tests fail, it hands a correction back to AIFactory's QA fixer,
which fixes the code, and TFactory re-tests. Left unbounded, that's an infinite
loop waiting to happen. v0.7.0 makes the handoff a **typed, versioned contract**
(#283) — a published JSON Schema AIFactory validates against, carrying the
failure signals, the AC mapping, and a hash of the *pinned assertion manifest* —
plus a **bounded-retry state machine**: after the cycle cap, instead of looping,
TFactory emits a terminal `needs_human` completion event. The line learns a human
is needed rather than waiting on a re-test that will never converge.

The assertion-pinning piece is subtle and matters. Each correction round
regenerates the test suite, which means assertions could quietly drift or weaken
between rounds and mask an unfixed bug. So the first failure snapshots the suite
to a manifest (per-assertion AST hashes); re-runs are **diff-gated** — a round
may only *add* assertions, never drop or loosen one. The manifest hash travels on
the contract, so CFactory can confirm round N tested against the same bar as
round 1. A correction loop that silently lowers its own standard isn't a fix —
it's a cover-up, and now it can't happen.

## …and the login that was turning everyone away

While deploying all of the above to the cluster, we hit a wall: Keycloak SSO
logged you in, then bounced you straight back to the login page. The fix took
three coordinated changes — the auth middleware now honors the OIDC
`access_token` cookie, the `/api/auth/me` route resolves it (it bypasses the
middleware), and the SPA stopped short-circuiting on a missing localStorage token
(#286). The login page also now prints the running version, so "which build is
this?" is never a guess again.

Two smaller wins rode along: the SaaS connector layer gained an opt-in
**visual/browser lane** with ServiceNow selector guidance (#173), and **SAP** got
its OData check template — all four connector platforms (ServiceNow, Salesforce,
MuleSoft, SAP) now test end-to-end through a stored credential (#111).

## Why this cycle, not later

None of this adds a feature you can screenshot. It's the difference between a
tool that works when you're watching and a service you can leave running. A
distributed line is only as trustworthy as its weakest handoff — and a handoff
that loses an event on a crash, can't be safely retried, or loops forever isn't a
handoff, it's a liability. v0.7.0 pays that down: **durable, idempotent,
traceable, bounded.** The boring words that let you stop watching.
