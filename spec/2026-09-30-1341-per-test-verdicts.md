---
status: draft
issue: 1341
intent: intent/2026-09-30-1341-per-test-verdicts.md
---

# Spec: the verdict reads the report, not the exit code

## What the measurements settled

| Question | Measured answer |
| --- | --- |
| Where does the wrong verdict come from? | `StabilityRun.ok` is `self.returncode == 0` (`stability_runner.py:73`). The Gradle lane runs the whole suite, so that code is 1 whenever **any** test in the project fails. |
| Is the per-test truth available? | **Yes, already on disk.** `.tf_gradle/junit.xml` from the failing run: `<testsuite name="BlockContactPreventionTest" tests="6" failures="2">` and `<testsuite name="OpenToFriendsToggleTest" tests="7" failures="0">`. |
| Can a test be mapped to its suite without generator changes? | **Yes on the JVM.** Every recorded test carries `test_file` (`OpenToFriendsToggleTest.kt`), and the public class name **is** the file name — a language rule. |
| Is there a parser to reuse? | `nix_env._junit_counts` already reads suites with regexes, deliberately not `xml.etree` (the whole-repo security gate rejects S314). |
| How many runs are affected? | All of them: every `StabilityRun` in `evaluator.py` (lines 1239, 1260, 1363) is built from an exit code alone. |

## Design

### 1. A run may carry the failure count for *its own* test

`StabilityRun` gains an optional `test_failures: int | None`, defaulting `None`.
`ok` becomes:

```python
@property
def ok(self) -> bool:
    if self.test_failures is not None:
        return self.test_failures == 0
    return self.returncode == 0
```

`None` means "no per-test result was found", and the exit code stays the
verdict — today's behaviour, preserved deliberately. The intent's constraint is
that no lane silently loses its safety net; a missing report must degrade, never
invent a pass.

### 2. The lane runners report the count for the test they were asked about

Each `run_*_lane_via_nix` already produces or merges a JUnit report. It gains a
`failures_for(test_file)` lookup that finds the suite belonging to that file and
returns its `failures + errors`, or `None` when no suite matches. The existing
regex pair (`_JUNIT_SUITE_RE`, `_JUNIT_ATTR_RE`) is reused — no XML parser, so
the security gate is unaffected.

Per-lane mapping, decided rather than generalised from the JVM rule:

- **gradle / maven (JVM):** suite name == the file's basename without extension.
- **pytest:** suites carry the file path in `name` or `classname`; match on the
  path, normalised to the repo root.
- **jest / vitest:** the reporter writes `name` as the test file path.
- **go-test:** package-level suites; match on the package directory.

Any lane whose mapping cannot be established returns `None` and keeps the exit
code — a lane is never made *less* correct by this change.

### 3. Absent from the report is `error`, not `consistent_fail`

When the lane produced a report but it contains **no** suite for the test, the
classifier records `error`, not `consistent_fail`. Absence means the class never
ran — a compile failure, or a mapping that missed — and calling that "the test
executed and its assertions failed" is precisely the false statement this issue
exists to remove.

This is distinct from case 1: no report at all (the lane never wrote one) leaves
`test_failures=None` and the exit code decides; a report that exists but omits
the test is an `error`.

### 4. `triaged_empty` stops being reachable when anything was rejected

The triager's terminal status distinguishes "nothing was produced" from
"everything produced was rejected". A run with rejections gets a status naming
that, so the cockpit cannot render "2 rejected, 14 committable" and "nothing
happened" identically.

## Alternatives rejected

- **Run each test alone** (`gradle test --tests <Class>`). Unambiguous, and it
  multiplies wall-clock by the test count — the measured run took ~50 minutes
  with three re-runs each. It also changes what is executed, so a test that only
  fails alongside its siblings would start passing.
- **Parse the lane's stdout for per-test lines.** The failing methods *are*
  printed (`BlockContactPreventionTest > … FAILED`), but the format is per
  runner and per version, and the structured report is right there.
- **Make the evaluator's judge LLM decide** which failures belong to the test.
  Non-deterministic where the data is exact, and #629 already moved in the
  opposite direction by giving the judge a deterministic failure-kind signal.
- **Have the generator emit the suite name.** Removes the mapping question, but
  needs a generator change for something derivable today, and leaves every
  existing recorded test unmappable.
- **Treat any suite failure as the test's failure when the test's own suite is
  clean.** That is the current behaviour, stated plainly.

## Risks

- **A real failure could be turned into a pass** — the intent's hard constraint.
  The guard is that `failures` for the test's own suite must be `0` *and* the
  suite must exist; the 2 genuine failures in the measured run are in the
  mapped suite and still reject. Verified by replaying the real artefacts.
- **The evidence rule must not weaken.** A zero exit with no report, zero tests,
  or recorded failures is still a failure — that check lives in the lane and is
  untouched. A per-test `ok` does not make a suite that never ran look healthy.
- **A wrong mapping silently accepts.** If `failures_for` matched the wrong
  suite, a failing test could read as clean. Mitigated by returning `None` on
  any ambiguity (zero or multiple matches) rather than guessing, and by a test
  that a two-suite report with similar names does not cross-match.
- **Flaky detection changes meaning.** `flaky` currently means "the exit code
  differed across runs"; it will now mean "this test's result differed", which
  is what it was always supposed to mean but is a behaviour change for anything
  reading those verdicts.

## Verification

Deterministic:

1. `StabilityRun(returncode=1, test_failures=0).ok` is True;
   `(returncode=0, test_failures=2).ok` is False; `test_failures=None` falls back
   to the exit code both ways. **Mutation:** drop the `test_failures` branch and
   the first case must fail.
2. `failures_for` returns `0` for `OpenToFriendsToggleTest.kt` and `2` for
   `BlockContactPreventionTest.kt` **against the real `junit.xml` from spec
   `026-myfriends-verify`**, which is committed as a fixture.
3. `failures_for` returns `None` — not `0` — when no suite matches, and when
   more than one matches.
4. A test absent from a present report classifies as `error`, not
   `consistent_fail`.
5. The triager's status is not `triaged_empty` when the rejection list is
   non-empty.

Replay, on the artefacts that produced the bug:

6. Feeding the real 16 recorded tests and the real `junit.xml` through the
   classifier yields **2 rejections and 14 non-rejections**. Today it yields 16.
   This is the whole issue, checked end to end on the data that exposed it.

Gates: ruff, `ruff format --check` over the CI path list, `ratchet_lint.py`
with its `--package` flags, the full suite, and each new test module collected
alone.
