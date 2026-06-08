# test-data-and-fixtures

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: fixtures,factories,builders,seed-data,determinism,db-seeding,reset,boundary-data,pytest,faker

---

# Test Data and Fixtures: Deterministic, Isolated, Reproducible

Use this skill when designing the data a test runs on — pytest fixtures, factories (factory_boy / Fakery), the builder pattern, deterministic seed data, database seeding and reset between tests, and choosing boundary values for inputs. Triggers: a generated test flaps because two tests share mutable state; you need a factory that produces a valid `User`/`Order` with sane defaults and one overridden field; a test relies on `random` or `datetime.now()` and fails the flake-lint guard; the DB isn't reset between cases so order-dependence creeps in; you need seed data that's the same on every run for the 3× stability signal; you're picking which concrete values to feed a boundary test. This skill keeps test data deterministic so TFactory's sandbox (`--network=none --read-only`) and 3× stability re-runs stay green for the right reasons.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Test Data and Fixtures: Deterministic, Isolated, Reproducible

Most flaky tests are flaky data, not flaky logic. TFactory runs every test three times for the `stability` signal and tracks cross-run flip-rate in flaky-history — so non-deterministic data gets caught and the test gets flagged or rejected. This skill is about building data that is identical every run, isolated per test, and expressive enough that the *interesting* value is obvious. It covers fixtures, factories, builders, seeding/reset, and boundary data, grounded in TFactory's sandbox constraints and flake-lint guards.

---

## When to use this skill
- Writing or reviewing pytest fixtures (scope, teardown, parametrisation).
- Building factories/builders for domain objects with valid defaults + targeted overrides.
- A test uses unseeded `random`, `uuid4`, `datetime.now()`, or wall-clock time.
- The DB carries state between tests, creating order-dependence.
- You need reproducible seed data so 3× stability and flaky-history stay stable.
- Choosing concrete boundary values (empty, max, off-by-one) to feed a test.

Do NOT trigger for:
- Deciding *which* boundary partitions exist — see `boundary-and-equivalence-testing` (this skill is the *data*, that one is the *case selection*).
- Designing the assertions on the data — see `assertion-design`.
- Choosing lanes/coverage — see `test-strategy`.

---

## Key principles
1. **Determinism is non-negotiable.** Seed every RNG, freeze every clock, fix every id. TFactory re-runs each test 3× and tracks flip-rate; any data that varies across runs will be flagged by `stability` or flaky-history.
2. **Isolate state per test.** Each test gets fresh data and tears it down. Shared mutable state creates order-dependence — the test passes alone and fails in the suite.
3. **Make the interesting value loud.** A factory supplies boring valid defaults so the one field that matters to *this* test is the only thing overridden — the reader sees intent immediately.
4. **Build valid by default, invalid on purpose.** Factories produce a passing-validation object out of the box; you deliberately break one field to test the failure path.
5. **No network, no real time in the sandbox.** The Executor runs `--network=none --read-only`. Fixtures must not fetch over the wire or write outside scratch — use in-memory or local seeded data.
6. **Seed data is code, not a dump.** Prefer programmatic seeding (factories) over giant SQL fixtures — it's diffable, parameterisable, and won't rot silently.
7. **Reset, don't accumulate.** Use transaction rollback or truncate-between-tests so the DB starts identical for every case.
8. **Boundary data lives next to the boundary test.** The empty string, the max int, the off-by-one date — name them clearly so a reviewer (and the LLM semantic check) sees what edge is exercised.

---

## Core concepts
**Fixture** — A pytest function (`@pytest.fixture`) that supplies a prepared object/resource and (via `yield`) tears it down. Scope (`function` / `module` / `session`) controls reuse; default to `function` for isolation.

**Factory** — A callable that produces a fully-valid domain object with overridable defaults (factory_boy `Factory`, or a plain function). Encapsulates "what a valid X looks like" so tests don't repeat construction.

**Builder** — Fluent, step-wise construction (`UserBuilder().with_role("admin").expired().build()`) for objects with many optional facets; reads as a sentence and keeps the deviation explicit.

**Deterministic seed** — Fixed RNG seed + frozen clock + fixed ids. `random.seed(0)`, `freezegun.freeze_time(...)`, explicit uuids. Guarantees byte-identical data across the 3× stability re-runs.

**DB seeding & reset** — Insert known rows before the test, roll back / truncate after. Transactional fixtures (rollback per test) are the cleanest reset; truncate is the fallback.

**Boundary data** — The concrete extreme values for a partition: `""`, `" "`, 0, -1, MAX, MAX+1, first/last valid date. The *values* the boundary analysis says to test.

**Flake-lint guard** — Gen-Functional's AST scan flags `random`-no-seed and `datetime.now`-no-freeze as flake risks (high/medium). Deterministic data design avoids the rejection.

---

## Common tasks
### A factory with sane defaults + one override
```python
def make_user(**overrides):
    base = dict(id=1, email="a@example.com", role="member",
                created_at=datetime(2026, 1, 1), active=True)
    return User(**{**base, **overrides})

def test_inactive_user_cannot_login():
    user = make_user(active=False)      # the ONE interesting field
    assert login(user) is False
```

### A function-scoped fixture with teardown
```python
@pytest.fixture
def temp_account(db_session):
    acct = make_account(balance=100)
    db_session.add(acct); db_session.flush()
    yield acct
    db_session.rollback()               # reset — next test starts clean
```

### Freeze time and seed RNG (avoid the flake-lint guard)
```python
from freezegun import freeze_time
import random

@freeze_time("2026-06-06T12:00:00Z")
def test_token_expiry():
    random.seed(0)                      # deterministic across 3× stability
    token = issue_token()
    assert token.expires_at == datetime(2026, 6, 6, 13, 0, tzinfo=timezone.utc)
```

### Transactional DB reset (no order-dependence)
```python
@pytest.fixture
def db_session(engine):
    conn = engine.connect(); txn = conn.begin()
    session = Session(bind=conn)
    yield session
    session.close(); txn.rollback(); conn.close()   # every test sees the same DB
```

### Parametrised boundary data
```python
@pytest.mark.parametrize("qty,ok", [
    (1, True),        # min valid
    (0, False),       # below min
    (999, True),      # max valid
    (1000, False),    # above max
])
def test_order_quantity_bounds(qty, ok):
    assert is_valid_quantity(qty) is ok
```

---

## Gotchas
1. **Module/session-scoped mutable fixture.** A `scope="module"` object mutated by one test leaks into the next → order-dependent flake, caught by 3× stability. Use `function` scope for anything mutable.
2. **`datetime.now()` in a fixture.** The flake-lint guard flags it (medium); worse, the data differs every run. Freeze the clock or inject a fixed timestamp.
3. **Unseeded `random` / `uuid4`.** High-severity flake-lint reject and a guaranteed stability flap. Seed the RNG and use fixed ids.
4. **Faker without a seed.** Faker is RNG-backed; `Faker.seed(0)` (or `factory_boy`'s seed) or it varies per run and the 3× re-runs disagree.
5. **DB not reset → passes alone, fails in suite.** The classic order-dependence bug. Use transactional rollback or truncate between tests; never rely on test execution order.
6. **Network call in a "unit" fixture.** Dies in `--network=none` sandbox. Mock the client or move the test to the `integration` lane with a real (sandbox-local) service.
7. **Giant static SQL/JSON fixture dump.** Rots silently as the schema evolves; nobody knows which row matters. Prefer factories so the relevant field is explicit and the rest is defaulted.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Shared session-scoped mutable fixture | Leaks state between tests; order-dependent flake fails 3× stability | Function scope for mutable data; teardown that resets |
| `datetime.now()` / wall-clock in data | Non-deterministic; flagged by flake-lint; re-runs disagree | `freeze_time` or inject a fixed timestamp |
| Unseeded `random` / `uuid4` / Faker | High-severity flake reject; stability flap | Seed the RNG (`random.seed`, `Faker.seed`); fixed ids |
| Constructing full valid object inline per test | Noise hides the one field under test; duplicated everywhere | Factory with defaults + targeted override |
| Relying on DB rows from a previous test | Order-dependence; passes alone, fails in suite | Transactional rollback / truncate between every test |
| Network/file fetch in a unit fixture | Fails in `--network=none --read-only` sandbox | Mock it, or move to `integration` with local seeded service |
| Huge static fixture dump (SQL/JSON) | Schema drift rots it; intent invisible | Programmatic factories; only the relevant field is set |
| Boundary values as magic numbers | Reviewer/LLM can't see which edge is tested | Name/parametrise them with labels (min, max, max+1) |
