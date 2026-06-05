---
layout: post
title: "TFactory stops working alone"
subtitle: "The test work now arrives governed, leaves on a schema the whole line can read, and shows up in the catalog. Three changes that turn a tool into a node."
date: 2026-06-05 12:00:00
author: DataSeek Team
---

For most of its life TFactory was a thing you handed work to. You finished a
feature, ran `/handover-to-tfactory`, and got back a graded test suite and a
triage report. Useful, but lonely — it inferred what "tested" meant, said "done"
in its own dialect, and lived nowhere anyone could find it.

This cycle that changed. TFactory is now a **node in the Factory line**, not a
tool beside it:

```
PFactory plans → AIFactory builds → TFactory verifies → CFactory watches
```

Three pieces of plumbing made the difference. None of them are glamorous. All of
them are the difference between a script and a service.

## The work arrives governed

Previously a handover carried code and a hope. The acceptance contract — what
actually counts as passing — lived in someone's head or got re-derived from the
diff. That's exactly the kind of guesswork that produces tests which are green
and meaningless.

Now TFactory picks up **governed test targets** from
[PFactory](https://pfactory.freundcloud.com/). PFactory has already planned and
governed the work; TFactory recognises those targets, enqueues them through the
normal pipeline, and — this is the part that matters — parses the
`pfactory:meta` block as the **test oracle**. The acceptance contract travels
*with* the work. The **Planner** plans against it; the **Evaluator** scores
against it. No re-derivation, no drift.

## It says "done" in a language the line understands

Four services that each announce completion their own way can't be watched as
one system. So the **Triager**'s terminal-status completion event now conforms
to **[RFC-0001](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0001-correlation-key-and-completion-event.md)**
— the canonical correlation-key and completion-event schema shared across the
whole Factory.

The key idea is a single `correlation_key`: the GitHub issue number, carried end
to end, with a synthetic `tf-<spec_id>` fallback so it is **never null**. One key
threads a unit of work from plan to build to verify. That's what lets
[CFactory](https://github.com/olafkfreund/CFactory) watch one contract instead
of four, and it's why the event is a real envelope now, not a flat blob:

```
{ "schema_version": ..., "event": "...completed",
  "service": "tfactory", "correlation_key": "224",
  "outcome": "passed", ... }
```

Two smaller things came with it: the default integration port moved 3102 → 3103,
and the Triager emits an RFC-0001 **usage block** on the event so a consumer
knows what it's reading. Full shape on [the completion-event
page](/completion-event-envelope/).

## It shows up in the catalog

A service nobody can find isn't part of the platform. TFactory now ships a
`catalog-info.yaml` and TechDocs and imports cleanly into **Backstage**, with
enriched annotations and an AI-assistant skill descriptor. Its TechDocs describe
the same RFC-0001 event the code emits — documentation and behaviour pinned to
each other on purpose.

## And a few reaches further

Alongside the spine work, TFactory can now reach more systems under test:
multi-step and SSO login flows for gated targets, `toHaveScreenshot` visual
baselines wired to the portal-managed store, and a fix that makes the Kubernetes
port-forward dispatch work against a live cluster. The backend suite is up to
2,803 tests.

## Why bother

The point was never integration for its own sake. A test result is only worth
acting on if you trust what it was measuring and you can see it land. Governed
pickup fixes the first — the tests grade against the contract that was planned,
not one TFactory guessed. The shared completion event and the catalog entry fix
the second — the result is visible to the system that's meant to act on it.

A tool tells you something. A node is part of how the work gets done.

See [the architecture](/architecture/) for how the agents fit together, or [the
completion-event envelope](/completion-event-envelope/) for the schema the line
now shares.
