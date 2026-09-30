---
status: approved
issue: 1341
author: olafkfreund
---

# Intent: a test's verdict comes from that test's result

## Problem

A verification run that found a real defect **and** produced 99 committable
tests reported as `triaged_empty` — nothing kept, nothing flagged, nothing for a
human to look at. Measured on spec `026-myfriends-verify`, a full verification of
the MyFriends demo build:

```
lane output, identical in all 48 runs:   360 tests completed, 2 failed
lane_runs.json:   16 tests, verdicts {"consistent_fail": 16}
verdicts.json:    every test "reject"
triage_report:    committed [], flagged [], rejected [16]
status:           triaged_empty
```

The 2 failures are real — `BlockContactPreventionTest` caught the subject code
checking blocking in one direction where the spec requires both
(pfactory-friends-demo#86). The other 14 generated tests passed and were
discarded with them.

**The cause is one property** (`agents/stability_runner.py:73`):

```python
@property
def ok(self) -> bool:
    return self.returncode == 0
```

A stability run's success is the **build's exit code**. The Gradle lane runs the
whole suite, so that code is 1 whenever *any* test in the project fails —
including tests unrelated to the one being evaluated. Two genuine failures made
all 16 verdicts `consistent_fail`, each carrying a reason that is false for 14
of them:

> "consistent test failure across 3 runs — the test executed and its assertions
> failed (subject behaviour is wrong), not an import/collection error"

The per-test truth was already in hand and thrown away: the lane merges
per-class JUnit XML into `junit.xml`, and the counts are already parsed
(`_junit_counts`) for the evidence rule.

## Proposed outcome

A test's verdict reflects that test's own result. A run where one generated test
fails and fourteen pass produces one rejection and fourteen candidates, and a
human sees both. The reason attached to a verdict is true of the test it is
attached to.

## Affected users and systems

- `agents/stability_runner.py` — `StabilityRun.ok` and whatever constructs it.
- The evaluator's lane runners, which produce the runs (`nix_env.run_*_lane_via_nix`
  already merge per-class JUnit XML).
- The triager and `quality_gate`, which consume the verdicts; and the cockpit,
  which shows `triaged_empty`.
- Not the evidence rule: a zero exit with no report, no tests, or recorded
  failures must still be a failure. That rule is what makes a green meaningful
  and this change must not weaken it.

## Constraints

- **Never turn a real failure into a pass.** The 2 genuine failures in the
  measured run must still reject. A change that makes everything pass is worse
  than the bug.
- **No lane may silently lose its safety net**: if a per-test result cannot be
  found for a test, the run's exit code stays the verdict — degrade to today's
  behaviour rather than inventing a pass.
- Works for every lane that produces JUnit XML (gradle, maven, pytest, go-test,
  jest), not only the one that exposed it.
- Proven on the real artefacts from spec `026-myfriends-verify`, which are still
  on disk: 16 tests, 2 of which must reject and 14 of which must not.

## Measured after writing this intent

Question 2 — how a generated test maps to its JUnit entry — is answered by the
artefacts of the failing run, so the spec does not have to guess:

```
lane_runs.json:  "open-to-friends-toggle-immediate-effect" -> OpenToFriendsToggleTest.kt
                 "block-prevents-requests-and-messages..." -> BlockContactPreventionTest.kt

.tf_gradle/junit.xml:
  <testsuite name="BlockContactPreventionTest" tests="6" failures="2"
  <testsuite name="OpenToFriendsToggleTest"    tests="7" failures="0"
```

Every recorded test already carries its `test_file`, and on the JVM the public
class name **is** the file name — a language rule, not a convention that drifts.
So the suite name is derivable without the generator emitting anything new, and
`failures="0"` vs `failures="2"` is exactly the per-test signal the verdict
needs. On this run that alone separates the 14 that passed from the 2 that
failed.

Still open for the spec: the equivalent mapping for pytest/jest/go-test (file
path rather than class name), and questions 1, 3 and 4 below.

## Approved answers (2026-09-30)

1. **Read the merged JUnit report**, not run each test alone. The data is
   already produced; running each alone would multiply a ~50-minute run by the
   test count.
2. **Map by the test's own `test_file`.** On the JVM the class name is the file
   name, so the suite name is derivable with no generator change. Other lanes
   map by file path in the report; the spec settles each one rather than
   assuming JVM's rule generalises.
3. **A test absent from the report is `error`, not `consistent_fail`.** Absence
   means the class never ran — a compile failure, or a mapping miss — and
   calling that "its assertions failed" is the same false statement this issue
   exists to remove.
4. **`triaged_empty` stops being reachable when rejections exist.** "2 rejected,
   14 committable" and "nothing happened" must not render identically.

## Open questions

1. **Read the report, or run the test alone?** Reading the merged `junit.xml`
   for the test's own class/case is cheap and uses data already produced. Running
   each test alone (`gradle test --tests <Class>`) is unambiguous but multiplies
   the wall-clock by the number of tests — this run took ~50 minutes with three
   re-runs each. I recommend reading the report.
2. **How is a generated test mapped to its JUnit entry?** By class name derived
   from the file, or by an explicit id the generator records? The former is
   convention and will drift; the latter needs the generator to emit it. This is
   the part most likely to be subtly wrong, and it decides whether the fix is
   robust or another heuristic.
3. **What should a test that is absent from the report mean?** It could be a
   compile failure (the whole class never ran) or a mapping miss. Those deserve
   different verdicts — `error` rather than `consistent_fail` — and conflating
   them is close to the bug being fixed.
4. **Should `triaged_empty` remain reachable at all** when rejections exist? The
   status is what the cockpit shows; "2 rejected, 14 committable" and "nothing
   happened" currently render identically.
