# coverage-analysis

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: coverage, line-coverage, branch-coverage, coverage-delta, cobertura, lcov, jacoco, vanity-coverage, coverage-target

---

# Coverage Analysis

Use this skill when interpreting or improving test coverage — line vs branch vs path coverage, telling *meaningful* coverage from *vanity* coverage, reading Cobertura/LCOV/JaCoCo reports, and reasoning about TFactory's `coverage_delta` signal (the new lines a specific test covers) and `coverage_target` gating. Triggers on coverage report, coverage percentage, uncovered branches, `.coverage`/`coverage.xml`/`lcov.info`/`jacoco.xml`, coverage gates failing, "we have 90% coverage but bugs ship", and the fact that browser-lane coverage is N/A.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Coverage Analysis

Coverage measures how much of the code under test was *executed* while the tests ran. It is a useful map of untested territory and a terrible measure of test quality — code can be 100% executed and 0% verified. The discipline is reading coverage as a *floor* ("these lines never ran, so they're definitely untested") rather than a *ceiling* ("these lines ran, so they're fine"). In TFactory, coverage feeds the Evaluator as `coverage_delta` — the new lines covered by *this specific test* — parsed from Cobertura (pytest), LCOV (Jest/Vitest), or JaCoCo (Java). It pairs with mutation testing: coverage says a line ran; mutation says the line was actually checked. The two together turn "we have 90% coverage" from a comforting number into an answerable question — *which* of those lines are merely executed, and which are genuinely verified.

---

## When to use this skill
- You need to interpret a coverage report and decide where to add tests.
- A coverage gate (`coverage_target`) is failing and you must close the gap meaningfully.
- TFactory's Evaluator reports a low or zero `coverage_delta` for a generated test.
- You suspect "vanity coverage" — a high percentage that hides untested logic.
- You're reasoning about branch vs line coverage for conditional-heavy code in the **unit**, **api**, or **integration** lanes.

Do NOT trigger for:
- The **browser** lane — code coverage of UI flows is effectively N/A; judge those by flow correctness.
- Treating a coverage number as a quality score — pair it with mutation testing instead.
- Chasing 100% on trivial getters/boilerplate where the marginal test has no value.

---

## Key principles
1. **Coverage is a floor, not a ceiling** — Uncovered lines are *certainly* untested; covered lines are only *maybe* tested. Use the report to find gaps, not to declare quality.

2. **Branch beats line** — Line coverage marks a line as covered if it ran at all; branch coverage requires *both* sides of every `if`/ternary/short-circuit. Conditional code needs branch coverage to mean anything.

3. **`coverage_delta` measures *this* test's contribution** — TFactory computes the new lines a given test covers (set math over the baseline). A test that only re-covers already-covered lines has a low delta and adds little.

4. **Coverage without assertions is vanity** — A line that executes but whose result is never asserted is theater. Coverage + mutation together tell the truth (see mutation-testing).

5. **Gate on meaningful targets, not vanity 100%** — `coverage_target` should gate the code that matters; forcing 100% drives developers to write assertion-free tests that game the metric.

6. **Read the format you have** — Cobertura XML (pytest), LCOV (Jest/Vitest), JaCoCo XML (Java) all encode hit/miss per line and per branch; the parser differs, the meaning is the same.

7. **Cover error and edge branches, not just the happy path** — The uncovered branches are usually the error handling and boundaries — exactly where bugs hide.

8. **Measure the diff, not the repo** — For a feature on a branch, what matters is whether *the new/changed lines* are covered, not the project-wide percentage. TFactory's coverage_delta is built for exactly this: a per-test attribution of new lines covered.

---

## Core concepts
**Line coverage** — Percentage of executable lines that ran at least once. The weakest useful metric.

**Branch coverage** — Percentage of decision outcomes (both true and false of each branch) exercised. Catches the "only tested the if, never the else" gap.

**Path coverage** — Percentage of distinct execution paths through the code. Exponential and rarely fully achievable; aim for the important paths.

**coverage_delta (TFactory signal)** — `coverage_delta.py` parses the Cobertura XML and uses set math to compute the *new* lines a specific test covers versus the baseline — the test's unique contribution, fed into the 0–1 confidence score.

**coverage_target (contract/gate)** — The threshold a run must meet; gating ensures generated tests actually move the needle, not just pass.

**Report formats** — Cobertura `coverage.xml` (pytest/`coverage.py`), LCOV `lcov.info` (Jest/Vitest/`c8`), JaCoCo `jacoco.xml` (Java). Each lists per-line hits and per-branch coverage.

**Vanity vs meaningful coverage** — Vanity: lines executed but results unasserted. Meaningful: lines executed *and* their behavior verified (confirm with the mutation signal).

---

## How TFactory uses this
Coverage is one of the Evaluator's five signals, but TFactory measures it as a *delta* attributable to a single test, not a project-wide percentage:

- **coverage_delta (`coverage_delta.py`)** — parses the run's Cobertura XML and uses set math (lines-covered-with-this-test minus the baseline) to compute the *new* lines this specific test contributes. A test that only re-covers covered lines has delta ≈ 0 and earns little, even when green.
- **coverage_target gating** — the contract's threshold ensures generated tests actually move coverage; a run that doesn't reach the target is gated.
- **Per-language formats** — pytest emits Cobertura `coverage.xml`, Jest/Vitest emit LCOV `lcov.info`, Java/JaCoCo emits `jacoco.xml`. The parser differs per lane; the line/branch hit data means the same thing.
- **Pairs with mutation** — coverage_delta and mutate-and-check are deliberately complementary: delta proves a line *ran* under this test; mutation proves the line was *verified*. A high delta with a SURVIVED mutant is the textbook vanity-coverage signature, and mutation (weighted higher) keeps the confidence score honest.
- **Browser lane** — code coverage is N/A for UI flow tests, so the Evaluator does not gate the browser lane on a coverage number.

---

## Common tasks
### Generate a branch-aware report (pytest)
```bash
pytest --cov=app --cov-branch --cov-report=xml --cov-report=term-missing
# coverage.xml is Cobertura; term-missing lists exact uncovered line numbers
```

### Find the gap that matters
Read `term-missing` / the Cobertura XML for the uncovered lines, then check whether they're error branches or boundaries — those are higher value than an uncovered logging line.
```python
# uncovered: the except branch
def parse(x):
    try:
        return int(x)
    except ValueError:        # ← if uncovered, add a test feeding a bad value
        return None
```

### Cover both branches (not just one)
```python
def test_is_eligible_both_branches():
    assert is_eligible(age=20) is True    # true branch
    assert is_eligible(age=15) is False   # false branch — branch coverage needs both
```

### Jest / Vitest with LCOV
```bash
jest --coverage --coverageReporters=lcov --coverageReporters=text
# lcov.info encodes DA: line hits and BRDA: branch data
```

### Java with JaCoCo + branch gate
```xml
<!-- jacoco maven plugin: fail under a branch threshold -->
<rule><limits><limit>
  <counter>BRANCH</counter><value>COVEREDRATIO</value><minimum>0.80</minimum>
</limit></limits></rule>
```

### Read a Cobertura line/branch entry
```xml
<!-- coverage.xml: hits=0 means never executed; condition-coverage shows branches -->
<line number="42" hits="0" branch="false"/>
<line number="50" hits="3" branch="true" condition-coverage="50% (1/2)"/>
```
Line 42 is dead-untested; line 50 ran but only *one of two* branches was taken — the gap branch-coverage exposes.

### Read an LCOV entry (Jest / Vitest / c8)
```
DA:42,0        # line 42, 0 hits → uncovered
BRDA:50,0,1,0  # block 50, branch 0, path 1, taken 0 times → uncovered branch
```

### Merge parallel coverage before gating
```bash
coverage combine .coverage.*      # python: merge per-worker data files
coverage xml                      # then emit a single Cobertura report
# jest: --coverage with a single run, or merge lcov via lcov-result-merger
```

### Interpret a low coverage_delta from TFactory
If the Evaluator reports `coverage_delta ≈ 0`, the test re-covered already-covered lines. Point the test at *uncovered* logic (an error path, a boundary, a new branch) so it contributes new lines — then confirm with the mutation signal that those lines are actually verified.

---

## Gotchas
1. **High line %, low branch %** — A function with one untested `else` can still show 95% line coverage. Always enable branch coverage (`--cov-branch`, JaCoCo BRANCH counter) for conditional code.

2. **Coverage counts execution, not verification** — Lines run inside a test with no assertion still count as covered. A SURVIVED mutant on a "covered" line proves it (see mutation-testing).

3. **Imports and decorators inflate the number** — Module-level code runs on import, padding line coverage without testing anything. Read the *function-body* coverage, not the file total.

4. **`# pragma: no cover` abused** — Excluding hard-to-test branches to hit a target hides exactly the risky code. Reserve pragmas for genuinely unreachable lines.

5. **coverage_delta of zero on a "new" test** — The test only re-exercised covered lines; it adds no new coverage even if green. Aim it at uncovered logic.

6. **Browser-lane coverage misread as a quality bar** — UI flow tests don't produce meaningful code coverage; don't gate them on a percentage.

7. **Combining coverage across parallel workers wrong** — Forgetting to merge `.coverage`/LCOV from parallel runs understates coverage and breaks the gate. Merge before reporting.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Treating % coverage as test quality | Covered ≠ verified; bugs ship at 100% | Pair coverage with the mutation signal |
| Line coverage only on branchy code | Misses untested `else`/error paths | Enable branch coverage everywhere conditional |
| Forcing 100% target | Drives assertion-free vanity tests | Gate `coverage_target` on code that matters |
| `# pragma: no cover` to hit a gate | Hides the riskiest, hardest code | Reserve pragmas for truly unreachable lines |
| Writing a test for `coverage_delta` with no asserts | Adds lines but verifies nothing; mutants SURVIVE | Assert behavior on the newly covered lines |
| Counting import-time lines as "tested" | Inflates the number meaninglessly | Read function-body coverage, not file totals |
| Gating browser-lane tests on coverage % | UI coverage is N/A; the number is noise | Judge browser tests by flow correctness |
| Not merging parallel-worker coverage | Understates coverage; false gate failures | Combine reports before computing the gate |
