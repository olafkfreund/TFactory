# property-based-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: property-based-testing, hypothesis, fast-check, invariants, generators, shrinking, stateful-testing, pytest, vitest

---

# Property-Based Testing

Use this skill when example tests feel insufficient and you want to assert *invariants* that must hold across a whole input space — round-trip encode/decode, idempotence, commutativity, ordering, conservation, or "never crashes". Triggers on Hypothesis (Python), fast-check (TypeScript/JavaScript), `@given`, `st.integers()`, `fc.assert`, `fc.property`, generators/arbitraries, shrinking a failing case to a minimal counterexample, stateful/model-based testing, and choosing between property tests and example tests in the TFactory unit and api lanes.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Property-Based Testing

Property-based testing generates hundreds of random inputs and checks that a *property* — a statement that should be true for every input — holds for all of them. Instead of "f(2) == 4", you assert "for all x, f(x) >= x". When a property fails, the framework *shrinks* the counterexample to the smallest input that still breaks it, handing you a minimal repro. In TFactory this is a high-value pattern for the unit and api lanes because property tests explore edge cases the LLM would never enumerate by hand, and they tend to KILL more mutants per line.

---

## When to use this skill
- The function has a clear algebraic property: round-trip (`decode(encode(x)) == x`), idempotence (`f(f(x)) == f(x)`), commutativity, monotonicity, or an invariant the output must always satisfy.
- You want to harden a parser, serializer, sort, dedup, money/decimal math, or state machine against edge cases.
- A bug was found from a weird input and you want a regression net broader than one example.
- You're writing tests in the **unit** or **api** lane (`pytest` + Hypothesis, `vitest`/`jest` + fast-check).
- You want tests that score well on TFactory's mutate-and-check signal — broad assertions kill more mutants.

Do NOT trigger for:
- The **browser** lane (Playwright/Cypress) — UI flows are example/flow-shaped, not property-shaped.
- Pure example assertions where the expected output is a single known constant (golden values).
- Non-deterministic code you haven't isolated yet — fix determinism first (see flaky-test-elimination).

---

## Key principles
1. **Test properties, not examples** — Ask "what must always be true?" not "what is f(3)?". A property covers an infinite input set; an example covers one point.

2. **The framework finds the counterexample, you state the law** — Your job is to articulate the invariant correctly. Hypothesis/fast-check do the searching and shrinking. A wrong property is worse than no property: it gives false confidence.

3. **Shrinking is the payoff** — When a test fails on `[0, 0, -2147483648]`, the framework will reduce it to the minimal trigger (often a single value). Trust the shrunk case; it isolates the bug.

4. **Round-trip is the highest-leverage property** — `parse(render(x)) == x` and `decode(encode(x)) == x` catch an enormous class of serialization bugs with one line.

5. **Constrain generators to the real domain, not the type** — If a function only accepts positive non-zero amounts, generate exactly those (`st.integers(min_value=1)`). Over-broad generators produce false failures; over-narrow ones miss bugs.

6. **Determinism is mandatory** — Property tests already randomize inputs; the *code under test* must be deterministic given an input. Seed everything (TFactory sets `PYTHONHASHSEED`); never let wall-clock or unseeded RNG leak in.

7. **Keep properties cheap** — They run hundreds of times. A property that does network I/O per example will dominate the 3× stability re-run budget. Use fakes (see test-doubles-and-mocking).

---

## Core concepts
**Property** — A boolean predicate over generated inputs that must hold for every input (e.g., "the output list is the same length as the input list").

**Generator / arbitrary** — A description of how to produce random inputs. Hypothesis calls them *strategies* (`st.lists(st.integers())`); fast-check calls them *arbitraries* (`fc.array(fc.integer())`).

**Shrinking** — After a failure, the framework repeatedly simplifies the input while the property still fails, converging on a minimal counterexample.

**Stateful / model-based testing** — Generate random *sequences of operations* against a system and a simple reference model, asserting the two stay in sync. Hypothesis: `RuleBasedStateMachine`; fast-check: `fc.commands`.

**`assume` / preconditions** — Discard generated inputs that don't meet a precondition (`hypothesis.assume(x != 0)`) instead of asserting on them. Overusing `assume` starves the generator — narrow the generator instead.

**Settings / examples budget** — `max_examples` controls how many cases run. TFactory's deterministic sandbox means the same seed reproduces the same cases run-to-run.

---

## How TFactory uses this
TFactory's Gen-Functional agent writes property tests into the **unit** and **api** lanes when the AC describes a law rather than a fixed example. The Evaluator then scores them through the same 5-signal pipeline as example tests:

- **coverage_delta** — property tests usually cover many branches at once because they drive the function with a wide input set, so the new-lines delta tends to be high.
- **3× stability** — the property runs hundreds of cases *per stability run*; if any of the three runs disagrees, the SUT is nondeterministic, not the framework. TFactory pins `PYTHONHASHSEED` and expects you to seed library RNGs so the generated cases are reproducible across the three runs.
- **mutate-and-check** — a well-stated invariant kills boundary and arithmetic mutants that a single example would miss, which is why property tests often earn the highest mutation weight in the confidence score.
- **cross-run flaky-history** — if a property flaps across runs (flip-rate ≥25%), the verdict is demoted accept→flag; the fix is always to seal a determinism leak in the code under test, never to lower `max_examples` to dodge the failing case.

Pin any counterexample the framework reports as an explicit `@example`/regression so a future deterministic run always re-checks it.

---

## Common tasks
### Round-trip property (Python / Hypothesis)
```python
from hypothesis import given, strategies as st

@given(st.text())
def test_encode_decode_round_trip(s):
    assert decode(encode(s)) == s
```
Generates arbitrary unicode, including the empty string, surrogate pairs, and control chars you'd never hand-pick.

### Invariant property (Python)
```python
@given(st.lists(st.integers()))
def test_sort_is_a_permutation_and_ordered(xs):
    out = my_sort(xs)
    assert sorted(out) == sorted(xs)      # same multiset (nothing lost/added)
    assert all(a <= b for a, b in zip(out, out[1:]))  # actually ordered
```
The second assertion KILLS a mutant that returns the input unchanged; the first KILLS a mutant that drops elements.

### Constrained generator (Python)
```python
@given(st.integers(min_value=1), st.integers(min_value=1))
def test_total_never_below_either_part(a, b):
    assert add_prices(a, b) >= a
    assert add_prices(a, b) >= b
```

### Round-trip + invariant (TypeScript / fast-check)
```typescript
import fc from 'fast-check';

test('JSON round-trips', () => {
  fc.assert(fc.property(fc.jsonValue(), (v) => {
    expect(JSON.parse(JSON.stringify(v))).toEqual(v);
  }));
});
```

### Stateful / model-based (fast-check sketch)
```typescript
fc.assert(fc.property(
  fc.commands([PushCmd, PopCmd, SizeCmd], { maxCommands: 100 }),
  (cmds) => {
    const real = new Stack();      // system under test
    const model = { items: [] };   // reference model
    fc.modelRun(() => ({ model, real }), cmds);
  }
));
```

### Reproducing a CI failure deterministically
Hypothesis prints `@reproduce_failure(...)` / a seed on failure. Pin it as an explicit `@example(...)` so the exact case becomes a permanent regression alongside the property.

### Composite generators (build structured inputs)
```python
@st.composite
def orders(draw):
    items = draw(st.lists(st.tuples(st.text(min_size=1),
                                    st.integers(min_value=1)), min_size=1))
    return {"items": items}

@given(orders())
def test_order_total_is_sum_of_lines(order):
    expected = sum(qty for _name, qty in order["items"])
    assert line_count(order) == expected   # independent oracle, not a re-impl
```
Composite strategies generate *valid domain objects* directly, avoiding `assume`-filtering of malformed ones.

### Metamorphic property (no oracle available)
When you can't compute the expected output, assert a *relationship between two runs*:
```python
@given(st.lists(st.integers()))
def test_sort_is_idempotent(xs):
    once = my_sort(xs)
    assert my_sort(once) == once   # sorting twice == sorting once
```
Metamorphic relations (idempotence, monotonicity, "adding an element only grows the result") catch bugs without an independent reference implementation.

---

## Gotchas
1. **Flaky property = non-deterministic SUT, not a flaky framework** — If the same seed passes and fails across TFactory's 3× stability run, the code under test has hidden state (clock, global RNG, dict order). Fix the SUT; the property is the messenger.

2. **`assume` death-spiral** — Filtering out most generated inputs makes Hypothesis give up with "too many filtered". Encode the precondition *in the generator* (`min_value`, `.filter` sparingly, `.map`) instead.

3. **Asserting the implementation, not the property** — Re-deriving the expected value with the same algorithm under test is a tautology that passes even when both are wrong. Use an *independent* oracle (a slow-but-obviously-correct reference, or a structural invariant).

4. **Mutable shared fixtures across examples** — A module-level list mutated inside the property leaks state between the hundreds of runs. Build fresh state per example.

5. **Floating-point equality** — `==` on floats fails on representable-but-not-equal values. Use tolerance (`math.isclose`, `fc.float` with care) or test exact decimal types instead.

6. **Time/timezone leaking into generators** — Generating `datetime.now()`-relative values makes the test depend on when it runs. Generate absolute instants and freeze the clock (see flaky-test-elimination).

7. **`max_examples` too low to find the bug** — A property that "passes" with 10 examples may fail at 200. For thin edge cases, raise the budget rather than trusting a green run.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Re-implementing the function as the oracle | Tautology — both copies share the same bug, mutant SURVIVES | Use an independent reference or a structural invariant |
| Generating the full type when the domain is narrow | Spurious failures on inputs the function never receives | Constrain the strategy/arbitrary to the real domain |
| `assume()` to discard most inputs | Generator starves; coverage collapses | Build the precondition into the generator |
| One giant property asserting many unrelated things | A failure can't be localized; shrinking is muddled | One property per law; multiple focused tests |
| Ignoring the shrunk counterexample | You debug a noisy 50-element input instead of the 1-element minimal repro | Read the shrunk case; pin it as an `@example` |
| Using property tests in the browser lane | UI flows aren't algebraic; coverage_delta is N/A there anyway | Keep property tests in unit/api lanes |
| Leaving the seed unpinned after a CI failure | The exact failing case may not regenerate | Pin `@example`/`@reproduce_failure` as a permanent regression |
| `==` on floats inside a property | Fails on equal-but-not-identical values | Use `math.isclose`/tolerance or exact decimal types |
