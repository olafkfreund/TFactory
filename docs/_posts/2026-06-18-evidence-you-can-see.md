---
layout: post
title: "Evidence you can see: screenshots, recordings, and a login that beats 2FA"
subtitle: "A green checkmark is not proof. The newest TFactory work makes the verdict legible — visible screenshots and recordings in the cockpit, an honest per-criterion ledger, and a real two-factor login captured on its way through."
date: 2026-06-18 12:00:00
author: DataSeek Team
---

A test pipeline earns trust by showing its work. For a while TFactory could tell
you "all five acceptance criteria verified" and even name the screenshots it
captured — but when you opened the portal there were no pictures. The evidence
existed as files on disk; nothing rendered it. This post is about closing that
gap, end to end, on real runs.

## The verdict, made legible

The browser lane now runs in a reproducible per-task Nix toolchain inside an
ephemeral Kubernetes Job (RFC-0005 Tier A). It drives the real deployed app,
captures a screenshot of each rendered page and a Playwright recording of the
test driving it, and writes them into the task's `findings/`. The portal serves
those bytes and renders them in two places.

The **Acceptance** tab is the honest headline. It maps each acceptance criterion
to the tests that exercise it and grades it `verified` only when one of those
tests actually passed — "verified X/Y", never a blanket "done":

![The Acceptance tab — verified 5/5, each criterion linked to its evidence]({{ '/static/img/screenshots/portal-acceptance.png' | relative_url }})

The **Evidence** tab is the gallery: the recordings play inline and the
screenshots render as thumbnails, so a reviewer can watch the test execute and
look at the page it produced — without shelling into a pod or digging through a
CI log:

![The Evidence tab — browser-lane recordings and screenshots]({{ '/static/img/screenshots/portal-evidence-recordings.png' | relative_url }})

The same evidence appears on the finished task in the
[CFactory](https://github.com/olafkfreund/CFactory) cockpit, so the one-pane view
over the whole Factory line shows the proof too.

### How we proved it

On the live cluster, against a small FastAPI app with five acceptance criteria
(a titled page, a heading, a Ping button that calls an API, and two JSON
endpoints): two consecutive browser runs, three of three generated specs passing
each time, six screenshots and five recordings captured per run. The screenshot
endpoint returns a valid PNG and the video endpoint a valid WebM; the acceptance
ledger reads verified 5/5, with the title, heading and ping-button criteria each
backed by a screenshot. The proof is the bytes the run produced, not a mock-up.

## A login that beats two-factor auth

The harder claim is authentication. Real acceptance criteria often hide behind a
login — and increasingly behind a one-time code. We do not bypass MFA. Following
the access model's Class C pattern, the pipeline provisions a **disposable
identity provider** (an ephemeral Keycloak), seeds a user whose OTP secret it
owns, and tears the whole thing down afterwards. Because it owns the secret, it
can generate valid [RFC-6238](https://datatracker.ietf.org/doc/html/rfc6238)
codes with its own generator — the same math the authenticator app on your phone
runs. The generated test declares a `fill_totp` login step and mints a fresh code
at the moment the form asks for it.

Here is the gate the test has to pass — the app, having accepted the username and
password, demanding the one-time code:

![The MFA one-time-code challenge]({{ '/static/img/screenshots/mfa-otp-challenge.png' | relative_url }})

And here it is on the other side, signed in, with the account console rendering
for the test user:

![The authenticated account console after the TOTP login]({{ '/static/img/screenshots/mfa-authenticated.png' | relative_url }})

A machine generated a time-based one-time code, submitted it to a real Keycloak,
and the IdP's own verifier accepted it — no human, no standing secret, fully torn
down at the end.

### The honest part

Building the MFA run surfaced a real bug, which is what runs built on real
evidence are supposed to do. The Playwright config that wires the login put the
saved-session setting in the global config, where it applied to *every* project —
including the "log in once" setup step whose job is to *create* that session.
So the setup died on the first line trying to read a session that did not exist
yet, and the entire authenticated-test path could never run. The fix was small
(scope the session to the project that reuses it); the lesson is not — we found
it because we insisted on a live login with a screenshot at the end, instead of
asserting success in a unit test. It is fixed, with a regression test.

## What changed, in one breath

The browser lane is reproducible (Nix in a k8s Job). Its screenshots and
recordings are visible in the portal and the cockpit. Each acceptance criterion is
graded against a test that actually ran. And authenticated targets — including
ones gated by a one-time code — can be tested against a disposable IdP with zero
production credentials, with the login captured as proof. "The tests passed"
became "here is the page, here is the click, watch the recording." That is the
difference between a checkmark and evidence.
