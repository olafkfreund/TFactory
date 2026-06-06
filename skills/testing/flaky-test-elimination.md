# flaky-test-elimination

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: flaky-tests, determinism, freezegun, fake-timers, seeds, auto-wait, iteration-order, stability, flip-rate

---

# Flaky Test Elimination

Use this skill when a test passes sometimes and fails other times with no code change — the classic flake. Triggers on determinism, freezing clocks (`freezegun`, `jest.useFakeTimers`), seeding randomness (`PYTHONHASHSEED`, `random.seed`, `faker.seed`), replacing `sleep` with auto-wait/explicit waits in Playwright/Cypress, dict/set iteration order, network nondeterminism, test isolation/ordering, and TFactory's two flake defenses — the Evaluator's 3× stability re-run and the cross-run flaky-history flip-rate (≥25% = flaky → demoted accept→flag).

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Flaky Test Elimination

A flaky test gives different results on identical code — green now, red on the next run. Flakes destroy trust in a suite: people start re-running until green and ignoring real failures. Every flake is a hidden *nondeterminism* leaking into the test: a real clock, an unseeded RNG, a `sleep` racing an async operation, hash-randomized iteration order, a shared fixture, or a network call. TFactory attacks flakiness from two sides: the Evaluator re-runs each test **3×** for the stability signal, and `flaky_history.py` tracks each test's pass/fail **across runs** so a chronically flaky test is caught even when one 3× window happens to pass — a flip-rate **≥25% flags it as flaky and demotes its verdict from accept to flag**.

---

## When to use this skill
- A test fails intermittently in CI but passes locally (or vice versa).
- TFactory's 3× stability signal shows inconsistent pass/fail, or flaky-history flip-rate ≥25%.
- A test depends on the current time, "now", today's date, or relative dates.
- A test uses `time.sleep`/arbitrary waits to "let things settle".
- Tests pass alone but fail when run together / in a different order.
- A test iterates a dict or set and asserts on order.

Do NOT trigger for:
- A test that fails *deterministically* every time — that's a real bug or a wrong assertion, not a flake.
- A genuinely slow-but-stable test — that's a performance concern, not flakiness.

---

## Key principles
1. **Determinism is the whole game** — Given the same code, a test must produce the same result every time, in any order, on any machine. Every flake is a determinism leak; find and seal the leak.

2. **Never assert on wall-clock time** — Freeze it. `freezegun`/`jest.useFakeTimers` make "now" a fixed, controllable value so timestamps and relative-date logic are reproducible.

3. **Seed every source of randomness** — `random`, `numpy`, `faker`, UUIDs. TFactory sets `PYTHONHASHSEED` in the sandbox so hash-based ordering is fixed; seed library RNGs explicitly too.

4. **Replace sleeps with conditions** — `sleep(2)` either flakes (too short) or wastes time (too long). Wait for the *condition* — Playwright/Cypress auto-wait on element state; in unit code, poll a predicate with a timeout.

5. **Tests must be order-independent and isolated** — No shared mutable state between tests. Each builds and tears down its own world; a leaked global is a flake waiting for a different run order.

6. **Don't assert on unordered iteration** — Dict/set iteration order is not a contract to test. Sort before comparing, or compare as sets.

7. **Quarantine, then fix — never delete-to-green** — A flake hidden by retries is a real failure you stopped seeing. TFactory's flip-rate surfaces it; fix the determinism rather than masking it.

---

## Core concepts
**Flake** — A test whose outcome varies without a code change, driven by hidden nondeterminism.

**3× stability (TFactory signal)** — `stability_runner.py` re-runs each test three times via the runner seam; inconsistent results across the three lower the stability signal in the 0–1 confidence score.

**Cross-run flaky-history / flip-rate (TFactory)** — `flaky_history.py` persists each test's pass/fail across runs (`<workspace>/<project>/test_history.json`). The *flip-rate* is how often it changes verdict run-to-run; **≥25% marks it flaky** and demotes an `accept` to `flag`, catching tests that pass one 3× window by luck.

**Auto-wait** — Playwright/Cypress retry actions/assertions until the element reaches the expected state or a timeout — the deterministic alternative to `sleep`.

**Frozen clock** — A test-controlled fixed "now" so time-dependent logic is reproducible.

**Seeded RNG** — A fixed seed making random sequences identical each run.

**Test isolation** — Each test owns its setup/teardown so order never changes outcome.

**The five flake sources** — In TFactory's experience, almost every flake is one of: (1) time/clock, (2) unseeded randomness, (3) async/sleep races, (4) iteration/hash order, (5) shared state or real network. Diagnose by category; each has a deterministic fix.

---

## How TFactory uses this
TFactory runs every generated test inside a deterministic Docker sandbox and defends against flakiness with two layered signals:

- **3× stability (`stability_runner.py`)** — each test is re-run three times via the runner seam. Disagreement among the three lowers the stability signal in the 0–1 confidence score. This catches *high-rate* flakes (those that fail roughly 1-in-3 or more).
- **cross-run flaky-history (`flaky_history.py`)** — pass/fail is persisted per test across *separate* runs in `<workspace>/<project>/test_history.json`. The flip-rate (how often the verdict changes run-to-run) catches *low-rate* flakes that happen to pass a single 3× window. **A flip-rate ≥25% marks the test flaky and demotes its verdict accept→flag.**

The sandbox already exports `PYTHONHASHSEED` so set/dict iteration order is pinned; you are responsible for the rest — freezing clocks, seeding library RNGs, replacing sleeps with condition waits, and isolating shared state. When a test is demoted for flip-rate, the correct response is to seal the determinism leak in the *code under test or the test setup*, never to add retries or lower assertions to force green.

---

## Common tasks
### Freeze the clock (Python)
```python
from freezegun import freeze_time

@freeze_time("2026-01-01 00:00:00")
def test_token_expiry():
    token = issue_token(ttl_minutes=30)
    assert token.expires_at == "2026-01-01T00:30:00"
```

### Fake timers (Jest / Vitest)
```typescript
beforeEach(() => jest.useFakeTimers().setSystemTime(new Date('2026-01-01')));
afterEach(() => jest.useRealTimers());

test('debounce fires after 300ms', () => {
  const fn = jest.fn(); const d = debounce(fn, 300);
  d(); jest.advanceTimersByTime(300);
  expect(fn).toHaveBeenCalledTimes(1);
});
```

### Seed randomness (Python)
```python
import random
def test_shuffle_is_reproducible():
    random.seed(1234)
    assert shuffled([1,2,3,4]) == [1,4,2,3]   # stable given the seed
```
TFactory already exports `PYTHONHASHSEED` so set/dict ordering is fixed in-sandbox.

### Replace sleep with auto-wait (Playwright)
```typescript
// ❌ await page.waitForTimeout(2000);
await expect(page.getByRole('status')).toHaveText('Saved');  // auto-waits
```

### Make iteration-order assertions deterministic
```python
def test_keys():
    result = build_index(records)
    assert sorted(result.keys()) == ["a", "b", "c"]   # sort, don't trust dict order
    assert set(result["a"]) == {"x", "y"}             # compare as a set
```

### Isolate shared state
```python
import pytest
@pytest.fixture
def cache():
    c = Cache()
    yield c
    c.clear()   # teardown so order never leaks state
```

### Poll a condition instead of sleeping (unit code)
```python
import time
def wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")

def test_job_completes():
    start_job()
    assert wait_until(lambda: job_status() == "done")
```
Waiting on the *condition* with a bounded timeout is deterministic where a fixed `sleep` races.

### Reproduce a flake locally before fixing
```bash
# hammer one test to surface a low-rate flake
pytest tests/test_x.py::test_flaky --count=50 -p no:randomly   # pytest-repeat
# or randomize order to expose isolation leaks
pytest tests/ -p randomly --randomly-seed=last
```

### Read TFactory's flake verdict
If `verdicts.json`/flaky-history shows flip-rate ≥25%, the test is demoted accept→flag. Open `test_history.json`, find which runs flipped, reproduce by re-running locally several times, then seal the leak (clock/seed/wait/order/network).

---

## Gotchas
1. **Mocking time in one module but not another** — Partial freezing still flakes when an unfrozen path reads the real clock. Freeze at every seam the code under test consults.

2. **`sleep` "fixes" a race only on fast machines** — A 2s sleep that works on your laptop flakes on a loaded CI runner. Wait on the condition, not the clock.

3. **Hash-order assumptions** — Relying on dict/set order passes locally with one `PYTHONHASHSEED` and fails under another. Sort or compare as sets; never assert insertion-implied order on a set.

4. **Shared module-level fixtures** — A list/dict defined at import time accumulates state across tests, so outcomes depend on run order — exactly what raises flip-rate. Build state per test.

5. **Unseeded UUIDs/Faker** — Random IDs leak into assertions and snapshots. Seed Faker, or assert on shape/regex rather than the exact random value.

6. **Real network in a "unit" test** — DNS hiccups and rate limits cause sporadic failures. Stub the boundary (see test-doubles-and-mocking).

7. **Passing the 3× window by luck** — A 10%-flaky test can pass three runs in a row. That's why flaky-history's cross-run flip-rate exists; don't trust a single green 3× window.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `time.sleep`/`waitForTimeout` to dodge a race | Flakes on slow machines, wastes time on fast ones | Wait on the condition (auto-wait / poll predicate) |
| Asserting on `datetime.now()` | Result depends on when it runs | Freeze the clock (`freezegun`/fake timers) |
| Unseeded `random`/Faker/UUID | Different values each run leak into asserts | Seed RNGs; assert on shape, not random value |
| Asserting dict/set iteration order | Order isn't a contract; varies by seed | Sort or compare as sets |
| Shared mutable module-level state | Outcome depends on run order; raises flip-rate | Per-test setup/teardown via fixtures |
| Real network/DB in a unit test | Sporadic external failures | Stub/fake the boundary |
| Auto-retry until green | Hides a real flake; erodes trust | Quarantine + fix the determinism leak |
| Trusting one green 3× run | A low-rate flake can pass it by chance | Check cross-run flip-rate before accepting |
