---
layout: post
title: "Tests that refuse to lie"
subtitle: "A build that looked fine failed one unicode edge case in twelve verdicts. The verifier capped it at VAL-0 and filed a handback rather than certify it. That refusal is the product."
date: 2026-07-19 12:00:00 +0000
author: DataSeek Team
---

On 2026-07-19 an autonomous run went through the Factory end to end: a plain
GitHub issue in, a tested pull request out, no humans in the loop. The part
worth writing about is not the build that passed. It is the one that did not.

Minutes before a clean run, a `slugify` helper built and looked correct. It
compiled, it read well, it did roughly what the task asked. Then TFactory
generated its tests and ran them, and one of twelve verdicts failed on a unicode
edge case. The verifier did not round up. It capped the result at **VAL-0** and
auto-filed a handback to fix the failing case. It refused to certify a build
with a failing test.

That refusal is the whole point. A verifier that shades a near-miss into a pass
is worse than no verifier, because it launders a defect into a green
checkmark that a human then trusts. TFactory is built so that cannot happen.

## How the verdict is computed from truth

TFactory does not accept a claim of "tests pass." It reconstructs the claim from
evidence, in a per-task Nix sandbox that is built fresh for the task and thrown
away after. Every task gets its own hermetic environment, so one run cannot
contaminate the next and a green result cannot come from a stale or borrowed
toolchain.

Inside that sandbox the generated tests actually run, and the result is graded
on five signals rather than a single pass/fail:

- **Coverage** — which lines the tests exercised.
- **Stability** — whether the verdict holds across repeated runs.
- **Mutation** — whether the assertions actually bite. On the hard lane TFactory
  perturbs the code under test and checks the tests notice. Assertions that
  survive a mutation are decoration, and they are reported as such.
- **Semantic relevance** — whether the tests test the thing that changed.
- **CI parity** — whether the local verdict matches what CI would say.

From those signals the run is assigned a **Verification Assurance Level (VAL)**,
recomputed from the truth of what happened, never asserted up front. The rule
that caught `slugify` is the same rule that governs every run: **a failing lower
lane caps the ceiling.** You cannot certify a high assurance level while a lane
beneath it is red. An untested dimension is reported as an honest gap, not
folded silently into a pass.

## The clean run, for contrast

The task that did pass was a `clamp(value, low, high)` helper. TFactory
generated nine tests and kept all nine, rejected none, killed the mutation
probe, and reported the result stable across three runs with confidence 0.96.
It graded the work **VAL-1, 5 of 5 acceptance criteria met.** VAL-2 and VAL-3
were reported as `not_run` — correctly, because a pure function has no API,
integration, or browser lane to exercise. The verifier did not invent lanes it
could not run just to post a bigger number. `not_run` is an honest verdict, and
saying so is part of refusing to lie.

Same machinery, two outcomes: one honest pass, one honest refusal. Neither was
massaged to look better than the evidence supported.

## The rough edge we are not hiding

The same run surfaced a real gap. The verify verdict is computed correctly, but
its auto-post back onto the pull request is currently gated behind a fix we have
filed as a TFactory issue. So on this run the verdict was right and the thread
back to the PR was not yet automatic. We are naming it here rather than letting
a demo gloss over it. A verifier that hides its own last rough edge has already
failed its own standard — the factory found the gap in its own feature and said
so.

## Why this matters

The failure mode that erodes trust in automated testing is not tests that
fail. It is tests that pass when they should not — green suites that certify
broken code because something in the pipeline preferred a clean-looking result
over a true one. TFactory is engineered against exactly that: sandboxed
execution so results are real, five signals so a passing line is not mistaken
for a working assertion, mutation testing so assertions have to earn their
keep, and a VAL ladder that can only be climbed with evidence. When the code is
wrong, the verdict says so and the build goes back.

## Watch it run

One continuous walkthrough of all four live portals with this run's own data:

<video controls preload="metadata" style="width:100%;max-width:960px;border-radius:8px" src="{{ '/assets/blog/2026-07-19/factory-walkthrough.mp4' | relative_url }}">
  Your browser does not support embedded video. <a href="{{ '/assets/blog/2026-07-19/factory-walkthrough.mp4' | relative_url }}">Download the walkthrough</a>.
</video>
