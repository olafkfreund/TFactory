# Reddit post — tests that refuse to lie

Suggested subreddits: r/programming, r/QualityAssurance, r/devops
Flair: consider "Show and tell" / "Discussion" where the subreddit requires it.

---

## Title

We built an autonomous test verifier that capped its own build at "unverified" over one failing unicode edge case, instead of rounding up to a pass

---

## Body

Short version of a run we did on 2026-07-19. An autonomous pipeline took a
plain GitHub issue, planned it, built it in a throwaway Kubernetes Job, opened
its own PR, generated tests, ran them, and graded the result — no human in the
loop. The interesting part was a build that did *not* pass.

A `slugify` helper built and looked correct. The verifier (we call it TFactory)
generated twelve test verdicts and ran them in a per-task sandbox. One failed on
a unicode edge case. The verifier did not shade that near-miss into a pass. It
capped the result at the lowest assurance level (VAL-0) and auto-filed a
handback to go fix the failing case. It refused to certify a build with a
failing test.

That refusal is the design goal. A verifier that quietly rounds a near-miss up
to green is worse than no verifier, because it launders a defect into a
checkmark a human then trusts.

How the verdict is built, since "our tests pass" is exactly the claim we don't
accept at face value:

- Generated tests run in a **per-task Nix sandbox**, built fresh and thrown
  away, so a green result can't come from a stale or borrowed toolchain.
- The result is graded on **five signals**, not one pass/fail: coverage,
  stability across repeated runs, mutation (perturb the code, check the
  assertions actually notice — surviving assertions are reported as
  decoration), semantic relevance, and CI parity.
- Those signals produce a **Verification Assurance Level**, recomputed from what
  actually happened. Core rule: **a failing lower lane caps the ceiling.** You
  can't claim a high assurance level with a red lane beneath it. Untested
  dimensions are reported as honest gaps, not folded into a pass.

For contrast, the run that did pass — a `clamp(value, low, high)` helper — got
9 tests generated and kept, 0 rejected, mutation probe killed, stable across 3
runs, confidence 0.96, graded VAL-1 with 5/5 acceptance criteria. VAL-2/VAL-3
were reported `not_run` because a pure function has no API/integration/browser
lane to exercise — the verifier didn't invent lanes to post a bigger number.

Honest caveat, because this was a real run and not a brochure: the verdict is
computed correctly, but its auto-post back onto the PR is currently gated behind
a fix we've filed as our own issue. On this run the grade was right; the thread
back to the PR wasn't yet automatic. Naming it rather than hiding it.

---

## Short FAQ

**Is this just running the test suite and reading the exit code?**
No. The exit code is the claim we don't trust. The verdict is reconstructed from
five signals in a hermetic sandbox, and mutation testing checks the assertions
actually fail when the code is broken.

**What's a VAL?**
Verification Assurance Level — how far a result was actually verified. It's
recomputed from evidence, and a failing lower lane caps how high it can go.

**Why cap at VAL-0 instead of just failing?**
Same effect for certification — a capped build isn't trusted and goes back —
but the level also records *how far* verification got before it hit the failure,
which is more useful than a bare fail.

**Why does mutation testing matter here?**
A test that runs but never fails is decoration. Mutating the code under test and
checking the tests notice is how we tell a real assertion from a passing line.

**Can I see it?**
There's a live walkthrough across the pipeline's portals available on request.
