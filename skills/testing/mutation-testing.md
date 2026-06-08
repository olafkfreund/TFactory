# mutation-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: mutation-testing, mutate-and-check, killed-survived, assertions, mutate-probe, stryker, pit, mutation-score

---

# Mutation Testing

Use this skill when a test is green but you suspect it doesn't actually *prove* anything — tautological assertions, asserting the wrong thing, or coverage without verification. Triggers on mutation testing, mutation score, KILLED vs SURVIVED mutants, writing assertions strong enough to catch a deliberately broken implementation, TFactory's mutate-and-check verdict signal, the per-language mutation backends (`mutate_probe` AST for Python, Stryker for TS, PIT for Java), `mutation_scope` from the contract, and interpreting a SURVIVED mutant as a missing or weak assertion.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Mutation Testing

Mutation testing flips the question from "does my code pass the tests?" to "do my tests catch a broken version of my code?". A tool introduces small faults (*mutants*) — flip a `<` to `<=`, drop a `+`, return a constant — then re-runs the tests. If a test fails, the mutant is **KILLED** (good — the test detects the fault). If all tests still pass, the mutant **SURVIVED** (bad — your tests can't tell correct code from broken code). In TFactory, mutate-and-check is one of the five Evaluator signals and carries the **highest weight** in the 0–1 confidence score, because a test that kills mutants is the strongest evidence a test is real.

---

## When to use this skill
- A test passes but you're unsure it would fail if the implementation were wrong.
- TFactory's Evaluator reports a SURVIVED mutant for a generated test, demoting its verdict.
- You're writing assertions for the **unit**, **api**, or **integration** lanes and want them to score on the mutate-and-check signal.
- You see high line coverage but suspect "vanity coverage" — code executed but not asserted.
- You're tuning `mutation_scope` (which symbols/lines the contract says are in-scope for mutation).

Do NOT trigger for:
- The **browser** lane — mutation/coverage signals are effectively N/A for UI flow tests.
- Code with no behavioral assertions at all (smoke tests) — add assertions first, then mutate.
- Generated boilerplate or pure data classes where every mutant is equivalent/trivial.

---

## Key principles
1. **A SURVIVED mutant is a hole in your assertions, not a tool quirk** — It means there exists a *wrong* implementation your tests accept. Find the mutated line, then strengthen the assertion that should have caught it.

2. **Assert the observable behavior, exactly** — Tests that only check "no exception raised" or `is not None` kill almost no mutants. Assert the precise value, length, ordering, and edge boundaries.

3. **Boundary mutants are the most revealing** — `<` → `<=`, `>` → `>=`, `+1` → `-1` survive unless you test *at* the boundary. Pin tests at off-by-one edges.

4. **Mutation weight dominates TFactory's confidence** — Among coverage_delta, stability, mutation, flake-lint, and semantic-relevance, mutation is weighted highest. A high coverage_delta with a SURVIVED mutant still yields low confidence.

5. **Kill the mutant in scope, don't chase equivalent mutants** — An *equivalent mutant* produces behavior indistinguishable from the original (e.g., `x*1`); it can never be killed and isn't a test failure. Respect `mutation_scope` so the Evaluator only counts meaningful mutants.

6. **One strong assertion beats ten weak ones** — A single assertion on the exact return value often kills more mutants than a pile of `assertTrue(x is not None)`.

7. **Mutation testing is per-language** — The backend differs (AST probe / Stryker / PIT) but the contract is identical: a passing test must fail on a deliberately broken implementation.

8. **Mutation is the antidote to vanity coverage** — Coverage tells you a line ran; only mutation tells you the test would notice if that line were wrong. When the two disagree (covered + SURVIVED), trust mutation.

---

## Core concepts
**Mutant** — A version of the source with one small deliberate fault injected (mutated operator, constant, return, or removed statement).

**KILLED** — At least one test failed on the mutant. The test suite detected the fault. This is the goal.

**SURVIVED** — All tests still passed on the mutant. The fault went undetected — your assertions are too weak or absent.

**Equivalent mutant** — A mutant whose behavior is identical to the original, so no test could ever kill it. Excluded from a fair mutation score.

**Mutation score** — KILLED / (KILLED + SURVIVED), excluding equivalents. Higher is better.

**mutate-and-check (TFactory signal)** — The Evaluator mutates ONE assertion-relevant element of the code under test and re-runs the generated test, recording KILLED or SURVIVED as the mutation signal — the highest-weighted input to the 0–1 confidence score.

**`mutation_scope` (contract field)** — Declares which symbols/lines are in scope for mutation, so the Evaluator targets the code this test is supposed to verify and skips out-of-scope noise.

**Per-language backends** — Python: `mutate_probe.py` (AST mutates one assertion/operator); TypeScript: Stryker (`lang_typescript/mutate_probe`); Java: PIT (planned/future). `mutation_dispatch.py` routes by `subtask.language`.

**Common mutation operators** — Arithmetic (`+`↔`-`, `*`↔`/`), relational (`<`↔`<=`↔`>`), logical (`and`↔`or`), boundary (`+1`↔`-1`), constant replacement (`return x`→`return None`/`0`), and statement deletion (drop a line). These are the faults a strong assertion must distinguish from correct code.

---

## How TFactory uses this
mutate-and-check is the single highest-weighted input to the Evaluator's 0–1 confidence score, because it is the most direct evidence a test *verifies* rather than merely *executes*. The flow:

1. `mutation_dispatch.py` reads `subtask.language` and routes to the right backend — `mutate_probe.py` (Python AST), Stryker (TS), or PIT (Java/future).
2. The backend mutates ONE assertion-relevant element of the code under test, scoped by the contract's `mutation_scope`, and re-runs the generated test via the runner seam.
3. The result is recorded as KILLED or SURVIVED in `findings/verdicts.json`.
4. A SURVIVED mutant pulls the confidence score down sharply even when coverage_delta is high — a coverage-high / mutation-SURVIVED test is the classic vanity-coverage signature.

Because mutation outranks coverage, the highest-leverage way to lift a generated test's verdict from flag toward accept is to strengthen the assertion that kills the surviving mutant — not to add more covered lines. Respect `mutation_scope`: asserting on out-of-scope helpers earns no mutation credit.

---

## Common tasks
### Turn a SURVIVED mutant into KILLED (Python)
A weak test:
```python
def test_discount():
    assert apply_discount(100, 0.1) is not None   # survives almost everything
```
The mutant `price * (1 - rate)` → `price * (1 + rate)` SURVIVES. Strengthen:
```python
def test_discount():
    assert apply_discount(100, 0.1) == 90          # kills the sign flip
    assert apply_discount(100, 0.0) == 100         # kills off-by-one on rate
```

### Kill a boundary mutant
```python
# is_adult uses age >= 18; mutant flips to age > 18
def test_boundary_18_is_adult():
    assert is_adult(18) is True    # KILLS the > / >= mutant
    assert is_adult(17) is False
```

### Kill a "drop the operation" mutant
```python
# total = subtotal + tax ; mutant drops + tax
def test_total_includes_tax():
    assert order_total(subtotal=100, tax=8) == 108   # SURVIVED if you only assert == 100
```

### Stryker (TypeScript)
```typescript
test('clamp respects upper bound', () => {
  expect(clamp(5, 0, 3)).toBe(3);   // kills the `value < max ? value : max` boundary mutant
  expect(clamp(3, 0, 3)).toBe(3);
});
```

### Reading TFactory's verdict
When `verdicts.json` shows a test's mutation signal as SURVIVED, open the mutated location the Evaluator reports, identify the behavior the mutant changed, and add the assertion that distinguishes original from mutant. Re-run; aim for KILLED to lift the confidence score.

### Respect mutation_scope
Only assert against symbols listed in the contract's `mutation_scope`. Asserting on out-of-scope helpers wastes effort on mutants the Evaluator won't count.

### Kill a logical-operator mutant
```python
# access_ok returns is_admin and is_active ; mutant flips `and` → `or`
def test_access_requires_both():
    assert access_ok(is_admin=True,  is_active=False) is False  # KILLS and→or
    assert access_ok(is_admin=False, is_active=True)  is False  # KILLS and→or
    assert access_ok(is_admin=True,  is_active=True)  is True
```
A single happy-path assertion (`True, True → True`) lets the `or` mutant SURVIVE; the two false cases distinguish `and` from `or`.

### Kill a constant-replacement mutant
```python
# retry uses max_attempts=3 ; mutant replaces 3 with 0 (or 1)
def test_retries_exactly_three_times(monkeypatch):
    calls = []
    monkeypatch.setattr("app.net.post", lambda *_: calls.append(1) or fail())
    with pytest.raises(RetryError):
        retry_post("payload")
    assert len(calls) == 3   # KILLS the 3→0 / 3→1 constant mutant
```

### Triage a SURVIVED report end to end
Open the location in the Evaluator's mutation result, read which operator/constant changed, decide whether it's an *equivalent* mutant (no observable difference — leave it) or a *real* gap (add the assertion that distinguishes original from mutant), then re-run and confirm KILLED.

---

## Gotchas
1. **High coverage, SURVIVED mutant** — The line ran but nothing checked its result. Coverage measures *execution*; mutation measures *verification*. Add a real assertion on the value.

2. **`assertTrue(result)` / truthiness traps** — Truthy assertions pass for `1`, `[x]`, `"anything"`, killing few mutants. Assert the exact value/shape.

3. **Equivalent mutants reported as SURVIVED** — `x + 0`, `x * 1`, or a refactor-neutral change can't be killed. Don't contort tests to kill them; confirm they're equivalent and rely on `mutation_scope` to exclude them.

4. **Testing the mock, not the code** — If collaborators (and the unit) are over-mocked, the real arithmetic never runs and every mutant SURVIVES. Mock only the boundary (see test-doubles-and-mocking).

5. **Only the happy path asserted** — Boundary and error mutants survive when you never test edges or error branches. Add off-by-one and failure-case assertions.

6. **Loose float assertions** — `assertAlmostEqual` with a huge tolerance lets arithmetic mutants slip through. Tighten the tolerance to the real precision requirement.

7. **Snapshot tests that auto-update** — A snapshot that re-records on change "passes" against mutants. Pin expected values; don't blind-update snapshots.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Asserting `is not None` / truthiness only | Survives nearly every mutant; weakest signal | Assert exact value, length, ordering |
| Chasing kills on equivalent mutants | Unkillable by definition; wastes effort | Confirm equivalence; exclude via `mutation_scope` |
| Treating high coverage as "tested" | Coverage = executed, not verified | Add value assertions; check the mutation signal |
| Only happy-path assertions | Boundary/error mutants SURVIVE | Test off-by-one edges and error branches |
| Over-mocking so real logic never runs | Every mutant SURVIVES — tautological test | Mock only the boundary, run the real code |
| Wide float tolerance | Arithmetic mutants slip through | Tighten tolerance to the real precision |
| Auto-updating snapshots | Snapshot re-records over the injected fault | Pin expected values explicitly |
| Asserting out-of-scope symbols | Mutants there aren't counted; no score gain | Target the contract's `mutation_scope` |
