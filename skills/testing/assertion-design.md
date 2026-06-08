# assertion-design

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: assertions,mutation-testing,tautology,one-behaviour-per-test,exception-assertions,evaluator,verdict,semantic-relevance

---

# Assertion Design: Strong, Mutation-Survivable, One Behaviour per Test

Use this skill when writing or reviewing the assertions inside a test — making them strong enough to kill mutants instead of tautological, keeping one behaviour per test, asserting on errors/exceptions correctly, and understanding why weak assertions get rejected or flagged by TFactory's Evaluator. Triggers: the `mutation` signal reports a SURVIVED mutant (the test ran but didn't catch the changed code); a test asserts `is not None` / `assert True` / "didn't throw" and you suspect it proves nothing; a test has five unrelated `assert`s about different behaviours; you need to assert a specific exception type and message; a verdict comes back `reject` or `flag` with low confidence and the cause is a weak assertion; the LLM `semantic_relevance` signal says the test doesn't verify the AC. This skill is about the single thing that most determines whether the Evaluator accepts a test: do the assertions actually pin down behaviour.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Assertion Design: Strong, Mutation-Survivable, One Behaviour per Test

A test is only as good as its assertions. Code can be executed (coverage), run three times without flapping (stability), and still prove nothing — if the assertion is tautological, the bug walks right through. TFactory's Evaluator exists to catch exactly this: the `mutate-and-check` signal deliberately breaks the code and a *good* test must then fail. This skill teaches assertions that survive mutation, isolate one behaviour, and verify the right exception — the assertions that earn an `accept` with high confidence.

---

## When to use this skill
- The `mutation` signal reports SURVIVED — the test didn't notice the code changed.
- A test uses `assert x is not None`, `assert True`, `assert result`, or "no exception raised" as its only check.
- A test bundles several unrelated assertions about different behaviours.
- You need to assert a specific exception type, message, and/or side effect.
- A verdict is `reject`/`flag` with low confidence and the assertion is the likely cause.
- `semantic_relevance` flags a test as not actually verifying its acceptance criterion.

Do NOT trigger for:
- Choosing which input values to assert against — see `boundary-and-equivalence-testing`.
- Building the data the assertion runs on — see `test-data-and-fixtures`.
- Deciding which lane the test lives in — see `test-strategy`.

---

## Key principles
1. **Assert the value, not its existence.** `is not None` / truthiness passes for almost any mutated return. Assert the *exact expected value* so a wrong-but-present result fails.
2. **Write to survive mutation.** Before committing, ask: "if someone flipped an operator or changed a constant here, would this assertion fail?" If not, the Evaluator's `mutate_probe` will mark it SURVIVED and downgrade the verdict.
3. **One behaviour per test.** Each test verifies one observable behaviour. Bundled assertions hide which behaviour broke and let the first failure mask the rest.
4. **The AC's verb is the assertion.** "shall reject" → assert the rejection happened; "displays total" → assert the rendered total equals the expected number. The assertion must match the requirement's claim.
5. **Assert error *type and message*, not just "it threw".** `pytest.raises(ValueError)` catching *any* ValueError lets a wrong-error mutant survive. Pin the message/match.
6. **Assert side effects explicitly.** If the behaviour writes a row, sends a message, or mutates state, assert that observable effect — not just the return value.
7. **Weak assertions are worse than no test.** They add coverage and a green check while proving nothing, creating false confidence and surviving every mutant. The Evaluator rejects them — write them strong the first time.
8. **Negative space matters.** Assert what should *not* happen too: the balance unchanged on a rejected withdrawal, no email sent on a failed signup.

---

## Core concepts
**Strong assertion** — Pins the exact expected outcome (`assert total == 4200`). Fails for any incorrect value. Kills mutants.

**Tautological / weak assertion** — Passes regardless of correctness (`assert result is not None`, `assert True`, `assert len(items) >= 0`). Survives every mutant; the Evaluator's `mutate-and-check` is built to expose it.

**Mutation-survivable** — A test that still passes after `mutate_probe` alters the code under test. SURVIVED = the test is weak; KILLED = the test is strong. The Evaluator dispatches per language (Python `mutate_probe`, TypeScript Stryker, Java PIT).

**One-behaviour-per-test** — Each test exercises a single behaviour with a single conceptual assertion (which may be a few asserts about the *same* behaviour). Makes failures diagnostic and mutation attributable.

**Exception assertion** — Verifying the *right* error: type + message/match + state-after. `pytest.raises(ValueError, match="expired")`.

**The 5-signal verdict** — `coverage_delta`, 3× `stability`, `mutate-and-check`, `flake-lint`, LLM `semantic_relevance` → accept/reject/flag + 0–1 confidence. Assertion strength most directly drives `mutate-and-check` and `semantic_relevance`.

**Semantic relevance** — The LLM's judgement of whether the assertion actually verifies the stated AC. A strong assertion on the *wrong* behaviour still gets flagged.

---

## Common tasks
### Turn a weak assertion strong
```python
# WEAK — survives almost any mutant
def test_discount():
    assert apply_discount(100, 0.2) is not None

# STRONG — kills constant/operator mutants
def test_discount_applies_twenty_percent():
    assert apply_discount(100, 0.2) == 80
```

### Split a multi-behaviour test
```python
# BUNDLED — first failure hides the rest, mutation can't attribute
def test_checkout():
    assert order.total == 80
    assert order.status == "paid"
    assert inventory.count == 9
    assert email.sent is True

# SPLIT — one behaviour each
def test_checkout_total_after_discount(): assert order.total == 80
def test_checkout_marks_order_paid():     assert order.status == "paid"
def test_checkout_decrements_inventory(): assert inventory.count == 9
def test_checkout_sends_confirmation():   assert email.sent is True
```

### Assert an exception precisely
```python
def test_withdraw_over_limit_raises():
    with pytest.raises(LimitExceededError, match="daily limit"):
        account.withdraw(10_000)
    assert account.balance == 500   # negative space: unchanged
```

### Make a test mutation-survivable on purpose
Pick the assertion that fails when the most likely mutation is applied:
```python
def test_is_adult_boundary():
    assert is_adult(18) is True      # kills >  -> >= would already pass; pair with:
    assert is_adult(17) is False     # this kills >= -> > and the off-by-one
```

### Assert a side effect, not just the return
```python
def test_signup_persists_user(db):
    signup("a@example.com")
    row = db.query(User).filter_by(email="a@example.com").one_or_none()
    assert row is not None and row.active is True   # the observable effect
```

---

## Gotchas
1. **`is not None` as the whole test.** Survives nearly every mutant; the Evaluator marks SURVIVED and the verdict drops. Assert the value.
2. **`pytest.raises(Exception)` too broad.** Catches the right error *and* every wrong one — a mutant that throws a different exception survives. Use the specific type + `match`.
3. **Snapshot/golden assertion on a huge blob.** Asserts everything and nothing; one whitespace change fails it, real bugs hide in the noise. Assert the specific fields that matter.
4. **Asserting the mock, not the behaviour.** `mock.assert_called_once()` proves the call happened, not that the result is correct — and survives logic mutations. Assert the outcome where possible.
5. **Multiple behaviours, one test.** When it fails you don't know which behaviour broke, and `mutate-and-check` can't attribute the kill. Split per behaviour.
6. **Assertion on the wrong thing.** Strong but verifying a behaviour the AC didn't claim — `semantic_relevance` flags it. Re-anchor the assertion to the AC's verb.
7. **Floating-point `==`.** `assert total == 0.1 + 0.2` flakes; the 3× stability won't save it because it's deterministically wrong. Use `pytest.approx`.
8. **"No exception" as success.** A try/except that swallows and asserts nothing passes when the code is broken. Assert the concrete result.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `assert x is not None` / `assert True` | Tautological; survives every mutant; Evaluator rejects | Assert the exact expected value |
| `pytest.raises(Exception)` (bare) | Wrong-error mutants survive; proves only "something threw" | Specific exception type + `match=` on the message |
| Bundling unrelated assertions in one test | First failure masks the rest; mutation can't attribute | One behaviour per test |
| `mock.assert_called` as the only check | Verifies the call, not correctness; logic mutants survive | Assert the observable outcome/return/side effect |
| Giant snapshot/golden of a whole object | Brittle to noise, blind to real bugs | Assert the specific fields the AC cares about |
| Strong assertion on the wrong behaviour | `semantic_relevance` flags: doesn't verify the AC | Anchor the assertion to the AC's verb |
| Floating-point `==` | Deterministically flaky/wrong; stability can't rescue it | `pytest.approx` / tolerance-based compare |
| try/except that asserts nothing on success | Passes while the code is broken | Assert the concrete expected result |
