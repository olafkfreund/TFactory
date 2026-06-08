# pytest-mastery

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: python,pytest,fixtures,parametrize,mocking,async,coverage,determinism,unit-lane,api-lane

---

# Pytest Mastery

Use this skill when writing or reviewing Python pytest tests for TFactory's unit and api lanes — covering fixtures (scope, autouse, yield teardown), parametrize, custom markers, conftest sharing, monkeypatch, pytest-cov coverage deltas, async tests with pytest-asyncio, eliminating time.sleep and wall-clock nondeterminism via freezegun, and property-based testing with hypothesis. Reach for this whenever a generated pytest file must be deterministic enough to survive the Evaluator's 3× stability and mutation signals.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Pytest Mastery

pytest is the engine behind TFactory's `unit` and `api` lanes. Tests run inside a `--network=none --read-only` Docker sandbox and are scored by the Evaluator on coverage delta, 3× stability, mutation kills, flake-lint, and LLM semantic relevance. A flaky or non-deterministic test gets rejected outright, so the craft here is as much about *determinism* as it is about assertions.

This skill covers the fixture model, parametrization, markers, mocking seams, async, and the determinism guardrails that keep generated tests green across re-runs.

---

## When to use this skill
- Writing pytest unit tests for pure functions, classes, or modules under test.
- Writing api-lane tests that exercise HTTP endpoints (FastAPI `TestClient`, `httpx`).
- Refactoring tests that the Evaluator flagged as flaky (stability < 3/3).
- Adding fixtures/conftest to share setup across a test package.
- Introducing parametrize to lift coverage delta without duplicating bodies.
- Replacing `time.sleep`/`datetime.now()` with deterministic equivalents.
- Do NOT trigger for: JavaScript/TypeScript tests (use jest-vitest-testing), browser end-to-end flows (use playwright-browser-testing), or Cypress suites (use cypress-testing).

---

## Key principles
1. **Determinism over cleverness** — TFactory re-runs every test 3× for the stability signal. Any reliance on dict/set ordering, unseeded `random`, wall-clock time, or network access fails. Pin everything.
2. **Arrange-Act-Assert, one behavior per test** — small focused tests give the mutation probe a clear assertion to flip; broad "god tests" survive mutants and hurt the score.
3. **Fixtures express setup, not assertions** — a fixture builds state and yields it; assertions live in the test body so failures point at the right line.
4. **Parametrize instead of copy-paste** — one parametrized test covers many inputs, raising coverage delta cheaply and keeping bodies DRY.
5. **Mock at the boundary you own** — patch the name where it is *looked up*, not where it is defined (`module_under_test.requests`, not `requests`).
6. **No real I/O in unit lane** — the sandbox blocks the network anyway; monkeypatch or inject fakes so the test fails fast and clearly instead of timing out.
7. **Assert on values, not just non-exceptions** — a test that only checks "didn't raise" gives the mutation probe nothing to kill and scores poorly.
8. **conftest is for sharing, not magic** — keep fixtures discoverable; deep autouse chains make failures hard to localize.

---

## Core concepts
**Fixture** — a function decorated with `@pytest.fixture` whose return (or `yield`) value is injected by name into any test that requests it as an argument. `yield` adds teardown after the test.

**Fixture scope** — `function` (default, fresh per test), `class`, `module`, `package`, `session`. Wider scope = shared state = faster but riskier for isolation; prefer `function` unless setup is expensive and read-only.

**autouse** — a fixture with `autouse=True` runs for every test in scope without being requested. Use sparingly (e.g. freezing time globally); overuse hides dependencies.

**parametrize** — `@pytest.mark.parametrize("arg", [...])` runs the test once per value, each a distinct test id, so one failure doesn't mask the others.

**marker** — a label (`@pytest.mark.slow`) for selecting/deselecting tests (`-m "not slow"`). Register custom markers in `pyproject.toml` to avoid warnings.

**monkeypatch** — built-in fixture to set/delete attributes, dict items, and env vars with automatic teardown — the deterministic alternative to leaving global state mutated.

**conftest.py** — auto-discovered fixture/plugin file; fixtures defined there are available to all tests in that directory tree with no import.

---

## Common tasks

### Fixtures with scope, yield teardown, and autouse
```python
import pytest

@pytest.fixture(scope="module")
def db_engine():
    """Expensive, read-only — build once per module."""
    engine = create_engine("sqlite:///:memory:")
    yield engine
    engine.dispose()  # teardown runs after the last test in the module

@pytest.fixture
def session(db_engine):
    """Cheap, isolated — fresh per test, rolled back after."""
    conn = db_engine.connect()
    txn = conn.begin()
    yield Session(bind=conn)
    txn.rollback()       # guarantees no cross-test leakage
    conn.close()

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Belt-and-braces: make any stray time.sleep a no-op so the suite stays fast and deterministic."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
```

### Parametrize, including expected-exception cases
```python
import pytest
from app.pricing import discounted_price, InvalidDiscount

@pytest.mark.parametrize(
    "price, pct, expected",
    [
        (100.0, 0,  100.0),
        (100.0, 25,  75.0),
        (100.0, 100,  0.0),
    ],
    ids=["no-discount", "quarter-off", "free"],
)
def test_discounted_price(price, pct, expected):
    assert discounted_price(price, pct) == pytest.approx(expected)

@pytest.mark.parametrize("pct", [-1, 101])
def test_discount_out_of_range_raises(pct):
    with pytest.raises(InvalidDiscount):
        discounted_price(100.0, pct)
```

### Mocking the boundary with monkeypatch (no network in the sandbox)
```python
# module_under_test.py imports: import requests
from app import weather

def test_fetch_temp_uses_response(monkeypatch):
    class FakeResp:
        status_code = 200
        def json(self):
            return {"temp_c": 21}

    # Patch where the name is LOOKED UP, not where requests is defined.
    monkeypatch.setattr(weather, "requests", type("R", (), {"get": staticmethod(lambda url, timeout: FakeResp())}))

    assert weather.current_temp("Oslo") == 21
```

### api-lane: FastAPI TestClient with dependency override
```python
import pytest
from fastapi.testclient import TestClient
from app.main import app, get_db

@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_create_user_returns_201(client):
    resp = client.post("/users", json={"email": "a@b.com"})
    assert resp.status_code == 201
    assert resp.json()["email"] == "a@b.com"
```

### Async tests with pytest-asyncio
```python
# pyproject.toml -> [tool.pytest.ini_options] asyncio_mode = "auto"
import pytest
import httpx
from app.main import app

@pytest.mark.asyncio
async def test_async_health():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
```

### Freeze time + property-based testing
```python
from freezegun import freeze_time
from app.invoice import due_date

@freeze_time("2026-01-15")
def test_due_date_is_30_days_out():
    assert due_date().isoformat() == "2026-02-14"

# Hypothesis interop — give it an explicit example for reproducibility,
# and a deadline so it never flakes on slow sandbox CPUs.
from hypothesis import given, settings, example, strategies as st
from app.text import slugify

@settings(deadline=None, max_examples=50)
@given(st.text())
@example("Hello World")
def test_slugify_is_idempotent(s):
    once = slugify(s)
    assert slugify(once) == once
```

---

## Gotchas
1. **Patching the wrong name** — `monkeypatch.setattr("requests.get", ...)` won't affect `from requests import get` in the module under test. Patch `module.get` (the lookup site). Fix: import the module, patch its attribute.
2. **Mutable default fixture values** — returning a shared `list`/`dict` from a `module`-scoped fixture lets one test's mutation leak into the next. Fix: use `function` scope or return a fresh copy.
3. **`datetime.now()` / `time.time()` in assertions** — flake-lint flags this as medium risk and the 3× stability run can flip on a clock tick. Fix: `freeze_time` or inject a clock.
4. **Unseeded `random` / set ordering** — high-risk flake patterns the linter rejects outright. Fix: `random.seed(0)` in a fixture, or assert on `sorted(...)`.
5. **`pytest.approx` forgotten for floats** — `0.1 + 0.2 == 0.3` is False; mutation/stability both punish brittle equality. Fix: `== pytest.approx(0.3)`.
6. **Forgetting `app.dependency_overrides.clear()`** — overrides leak into other test modules sharing the app instance. Fix: clear in fixture teardown.
7. **`asyncio_mode` not set** — async tests silently pass without running (collected but skipped). Fix: set `asyncio_mode = "auto"` or mark each test.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `time.sleep(2)` to wait for something | Wall-clock dependency; slow + flaky, flake-lint flags it | Mock the clock, or restructure so the result is synchronously available |
| One test asserting 10 unrelated things | Mutation probe can't isolate; one break hides others | One behavior per test; parametrize variations |
| `assert result is not None` as the only check | Mutants survive (nothing meaningful flips); low score | Assert the actual value/shape returned |
| `session`-scoped DB fixture with writes | Cross-test state leak breaks 3× stability | `function` scope with transaction rollback |
| Hitting a real URL in a unit test | Sandbox is `--network=none`; test times out | monkeypatch the HTTP client with a fake response |
| Unregistered custom markers | `PytestUnknownMarkWarning` noise, typo'd markers silently no-op | Register in `[tool.pytest.ini_options] markers = [...]` |
| Asserting on `dict.keys()` order | Insertion order is impl detail; flake-lint high risk | Compare against a set or sort first |
| `@given` with no `deadline` override | Slow sandbox CPU trips Hypothesis's default deadline → flake | `@settings(deadline=None)` and a capped `max_examples` |
