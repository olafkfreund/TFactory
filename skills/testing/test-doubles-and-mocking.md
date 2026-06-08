# test-doubles-and-mocking

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: test-doubles, mocking, stubs, spies, fakes, monkeypatch, unittest-mock, jest, mockito, isolation

---

# Test Doubles and Mocking

Use this skill when a unit test needs to isolate the code under test from a collaborator — a network call, a clock, a database, a third-party SDK — and you must decide between a mock, stub, spy, fake, or dummy. Triggers on `unittest.mock`, `pytest` `monkeypatch`, `patch`, `MagicMock`, `jest.fn()`, `jest.mock()`, `vi.fn()`, Mockito `mock`/`verify`/`when`, "mock the API", "stub the DB", "fake the clock", over-mocking smells, and the cardinal rule: never mock the unit under test.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Test Doubles and Mocking

A *test double* is any object that stands in for a real collaborator during a test. The five canonical kinds — dummy, stub, spy, mock, fake — differ in how much behavior they carry and what they let you assert. Choosing the wrong one produces brittle tests that break on refactors or, worse, tests that pass while the real integration is broken. In TFactory's unit and api lanes, well-chosen doubles keep tests deterministic (no network, no clock drift) and keep coverage_delta honest — they exercise *your* code, not the collaborator's.

---

## When to use this skill
- The code under test calls out to something slow, non-deterministic, or with side effects: HTTP, DB, filesystem, time, randomness, payment SDK, message queue.
- A test is flaky or slow because a real dependency is in the loop (hand off determinism work to flaky-test-elimination).
- You need to assert that a collaborator *was called* a certain way (a command/side-effect), or feed it canned return values (a query).
- You're isolating one unit in the **unit** lane, or stubbing an upstream service in the **api**/**integration** lane.

Do NOT trigger for:
- The **browser** lane — prefer real backends behind Playwright/Cypress, or network interception at the boundary, over deep mocking.
- Pure functions with no collaborators — they need no doubles; mocking them adds noise.
- The unit under test itself — mocking what you're testing means you test the mock.

---

## Key principles
1. **Never mock the unit under test** — If you stub the very function you claim to verify, the test asserts the stub's behavior, not the code. The mutate-and-check signal will show every mutant SURVIVING because nothing real runs.

2. **Mock across boundaries, not within them** — Double the *seams* your unit talks to (network, clock, DB), not its internal helpers. Over-mocking internals couples the test to implementation and breaks on every refactor.

3. **Stub queries, mock commands** — For *queries* (calls that return data, no side effect) use a stub that returns canned data and don't assert the call. For *commands* (calls with a side effect) use a mock/spy and assert the call happened correctly.

4. **Prefer fakes over deep mock chains** — A small in-memory fake (a dict-backed repository) is more robust and readable than `mock.return_value.query.return_value.filter.return_value.first.return_value`.

5. **Patch where it's looked up, not where it's defined** — `patch('mymodule.requests.get')` targets the name the code under test actually references. Patching the origin module misses the bound reference.

6. **Assert behavior, not interactions, when you can** — Over-verifying call counts/args makes tests fragile. Verify interactions only for genuine commands; otherwise assert the observable result.

7. **Reset/auto-undo doubles between tests** — Leaked patches cause order-dependent flakiness. Use context managers, fixtures, or `jest.restoreAllMocks()`; never leave a global patched.

---

## Core concepts
**Dummy** — A placeholder passed to satisfy a signature but never used (e.g., an unused `logger` arg).

**Stub** — Returns hard-coded answers to calls; carries no assertions. Used to feed *queries* (`get_user()` returns a canned user).

**Spy** — A real or recording object that captures how it was called so you can assert later, often wrapping real behavior.

**Mock** — A double pre-programmed with expectations; the test *verifies* it was called as expected. Used for *commands* (`send_email()` was called once with this address).

**Fake** — A working but simplified implementation (in-memory DB, fake clock) suitable only for tests. The most robust double for complex collaborators.

**Seam** — The point where you can substitute a double without changing the code under test: a constructor arg, a module-level import, an injected dependency.

**State vs interaction testing** — State testing asserts the *result*; interaction testing asserts the *calls*. Prefer state testing; reach for interaction testing only for side effects you can't otherwise observe.

---

## How TFactory uses this
Test doubles are how TFactory's generated tests stay deterministic inside the Docker sandbox (`--network=none --read-only`): real HTTP, real clocks, and real databases simply aren't reachable, so collaborators *must* be doubled at the boundary. This interacts with the Evaluator's signals:

- **3× stability** — doubling time/network/randomness is what makes a test pass three identical runs. A real boundary call would flap and tank the stability signal.
- **mutate-and-check** — the cardinal rule (don't mock the unit under test) maps directly to mutation score: if the code under test is stubbed, the mutated logic never executes and every mutant SURVIVES, yielding low confidence. Mock *only* the seam so the real arithmetic runs and mutants can be KILLED.
- **coverage_delta** — over-mocking internal helpers means those lines never execute under your test, suppressing the delta. Mock the boundary, run the internals.
- **flaky-history** — leaked/un-reset patches cause order-dependent failures that drive the cross-run flip-rate ≥25% and demote accept→flag. Always auto-undo doubles (fixtures, context managers, `restoreAllMocks`).

Prefer fakes for the api/integration lanes (an in-memory repo or a recorded-response server) and lightweight stubs/mocks for the unit lane.

---

## Common tasks
### Stub a query (pytest monkeypatch)
```python
def test_greeting_uses_display_name(monkeypatch):
    monkeypatch.setattr("app.users.fetch_user",
                        lambda uid: {"name": "Ada"})   # stub, no assert
    assert greet("u1") == "Hello, Ada"
```

### Mock a command and verify it (unittest.mock)
```python
from unittest.mock import patch

def test_signup_sends_welcome_email():
    with patch("app.signup.send_email") as send:   # patch where it's used
        signup("ada@x.com")
        send.assert_called_once_with("ada@x.com", template="welcome")
```

### Fake a collaborator (in-memory repository)
```python
class FakeRepo:
    def __init__(self): self._db = {}
    def save(self, k, v): self._db[k] = v
    def get(self, k): return self._db.get(k)

def test_service_persists():
    svc = Service(repo=FakeRepo())   # injected seam
    svc.create("a", 1)
    assert svc.repo.get("a") == 1
```

### Mock a module (Jest / Vitest)
```typescript
import { sendEmail } from './mailer';
jest.mock('./mailer');               // vi.mock('./mailer') in Vitest

test('signup sends one email', () => {
  signup('ada@x.com');
  expect(sendEmail).toHaveBeenCalledTimes(1);
  expect(sendEmail).toHaveBeenCalledWith('ada@x.com', 'welcome');
});
afterEach(() => jest.restoreAllMocks());
```

### Stub + verify (Mockito / JUnit 5)
```java
PaymentGateway gw = mock(PaymentGateway.class);
when(gw.charge(100)).thenReturn(Receipt.ok());   // stub the query
Checkout checkout = new Checkout(gw);
checkout.pay(100);
verify(gw).charge(100);                           // verify the command
```

### Freeze the clock (a fake, not a mock)
```python
from freezegun import freeze_time
@freeze_time("2026-01-01")
def test_timestamp_is_frozen():
    assert make_record()["created"] == "2026-01-01T00:00:00"
```

### Spy on a real object (record without replacing behavior)
```python
def test_cache_is_consulted(monkeypatch):
    real = Cache()
    spy = monkeypatch.setattr(real, "get",
                              lambda k, _orig=real.get: _orig(k))  # wraps real
    service = Service(cache=real)
    service.load("k")
    # assert the observable result; verify the call only if it's the contract
    assert service.load("k") == real.get("k")
```
Spies let you assert interactions *while* real behavior still runs — useful when both the result and the call matter.

### autospec to catch signature drift (unittest.mock)
```python
from unittest.mock import create_autospec
import app.mailer as mailer

def test_signup_signature_safe():
    fake_send = create_autospec(mailer.send_email)   # matches real signature
    fake_send("ada@x.com", template="welcome")       # OK
    # fake_send("ada@x.com", subj="x")  → TypeError: unexpected kwarg
```
`autospec` makes the double reject calls the real function would reject — preventing tests that pass against a signature that no longer exists.

---

## Gotchas
1. **Patching the wrong target** — `from x import get; patch('x.get')` works, but if the code did `import x; x.get()` you must patch `x.get`. Patch the *reference the code under test resolves at call time*.

2. **`MagicMock` happily accepts anything** — `mock.anything.you.want()` never errors, so a typo'd attribute silently "passes". Use `autospec=True` / `create_autospec` so the double matches the real signature and rejects bad calls.

3. **Leaked patch across tests** — A `patch` started but not stopped (no context manager/decorator) bleeds into later tests, causing order-dependent failures and inflating cross-run flaky-history flip-rate.

4. **Over-verifying interactions** — Asserting every call and arg of a chatty collaborator makes the test break on harmless refactors. Verify only the meaningful command.

5. **Mocking returns the wrong type** — A stub returning a bare `MagicMock` where real code expects a list/str causes confusing downstream failures. Return realistic shapes; a fake is often clearer.

6. **Mock so total the test is a tautology** — If every collaborator and the unit's own helpers are mocked, mutants SURVIVE because no real logic executes. Mock only the boundary.

7. **Time/random mocked in one place but not another** — Partial determinism still flakes. Centralize clock/RNG behind one seam and fake that single seam (see flaky-test-elimination).

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Mocking the function under test | You test the mock, not the code; all mutants SURVIVE | Mock only collaborators at the boundary |
| Deep mock chains (`m.a.return_value.b...`) | Brittle, unreadable, couples to call structure | Use a small in-memory fake |
| `MagicMock()` without `autospec` | Accepts misspelled/wrong-signature calls silently | `create_autospec` / `autospec=True` |
| Patching where the symbol is *defined* | Bound reference in caller isn't affected | Patch where the caller *looks it up* |
| Asserting call counts on pure queries | Fragile; queries have no side effect worth verifying | Assert the returned/observable result |
| Forgetting to reset/restore mocks | Order-dependent flakiness, raises flip-rate | Context managers / fixtures / `restoreAllMocks` |
| A real network/DB call "just for this test" | Non-deterministic, slow, fails 3× stability | Stub/fake the boundary |
| Stub returns a `Mock` where a dict/list is expected | Confusing downstream errors far from the cause | Return realistic data shapes |
