---
layout: post
title: "A benchmark number is only as honest as the verifier behind it"
subtitle: "This cycle the fleet published its first external score. The reason that number is worth trusting is the same discipline TFactory was built on: verification with visible evidence, and no credit for work that was not actually run."
date: 2026-07-13 12:00:00
author: DataSeek Team
---

TFactory is the Verify stage of the Factory pipeline. It takes the branch
AIFactory built and decides, with evidence, whether it actually meets the
acceptance criteria the plan set out. This cycle the fleet did something it had
never done: it graded itself against an external benchmark and published the
result. That is only meaningful because of the discipline this repository exists
to enforce.

## Why verification is the load-bearing part

An autonomous coding pipeline can claim any success rate it likes if it is
allowed to score its own homework. The Factory hub deliberately does not. The
July baseline pushed 50 real tasks through the live pipeline and scored them with
the official external harness, never with our own judgment. When the pipeline
emitted a scorable patch, it was right about four times in five. That ratio is
worth something precisely because a trustworthy verdict is what TFactory is for.

The most important thing a verifier can do is refuse to pass work that did not
run. A green dashboard that reports success for a build that quietly produced no
code is worse than a red one, because it hides the failure. The same instinct
that drives our Verification Assurance Levels, our regression suite, and our
insistence on visible proof over a claimed pass is the instinct that made the
benchmark useful: it counted the empty patches as failures instead of pretending
they were anything else.

## What this proves

That honesty is a system property, not a slogan. The benchmark exposed that
nearly half of the coder's failures were silent no-ops, and it could only do that
because the pipeline is built to distinguish real, evidenced success from the
absence of it. A factory that verifies for real is a factory that can be measured
for real.

It also proves the case for a dedicated verification stage. If the thing that
builds the code is also the thing that decides whether the code is good, you have
no independent check. Splitting verification out, giving it its own evidence
trail and its own standard for what done means, is what lets the whole system
make a claim a sceptic can accept.

## What is next

Keep raising the bar on evidence: extend the verification lanes so more of what a
change touches is exercised for real, and make the scored-resolve confirmation
for the fleet's routing runs part of the standing regression matrix. The goal is
simple and unglamorous. When the factory says a change is done, that word should
mean exactly what an outside auditor would take it to mean.
