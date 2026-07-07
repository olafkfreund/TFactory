---
layout: post
title: "Verification joins the shell — and takes the screenshots for this post"
subtitle: "TFactory picks up the fleet-wide shell and the latest models. The same MFA browser harness that verifies the portals captured every authenticated screenshot you see here. A one-page showcase is ready to download."
date: 2026-07-07 06:00:00
author: DataSeek Team
---

TFactory verifies what the factory builds: it runs real tests on real code and
reports verdicts where humans look. This round of work connected it to the rest of
the family, moved it onto the current models — and, fittingly, its own portal-test
harness produced the screenshots in this post.

![TFactory verify pipeline]({{ '/assets/blog/2026-07-07/verify-pipeline.png' | relative_url }})

## Part of one product now

- **A portal switcher** in the top bar moves you between Plan, Build, Test, and
  Cockpit as one product.
- **A global command palette** — Cmd-K — searches every portal's work and jumps
  straight to it.
- **A fleet "needs you" badge** shows how many tasks across the factory are waiting
  on a human.
- **Silent single sign-on** carries you in from any sibling portal without a second
  login.

## It tests the portals, too

TFactory's portal-UI harness logs into every portal through **real Keycloak
multi-factor authentication**, drives every menu and dialog, and captures
screenshots and verdicts. Every authenticated screenshot in this post and in the
downloadable showcase was captured by that harness against the live product —
verification and marketing from the same source of truth.

That harness even caught a real regression while taking these shots: a login
redirect that returned a 404 on two portals. It was fixed the same day.

## On the latest models

Verification now runs on the current model lineup, defaulting to **Claude Opus
4.8**, with the continuous regression suite — retry, quarantine, drift and impact
analysis — running nightly and on demand.

## Download the showcase

**[TFactory — one-page showcase (PDF)]({{ '/assets/tfactory-showcase.pdf' | relative_url }})**

## The path forward

Next: broader language and browser coverage, deeper deployment verification, and
verdicts an operator can trust without re-checking by hand.
