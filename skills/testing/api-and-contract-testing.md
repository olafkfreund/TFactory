# api-and-contract-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: api,rest,graphql,httpx,supertest,openapi,json-schema,pact,contract-testing,pagination,idempotency,auth

---

# API and Contract Testing

Use this skill when writing or reviewing TFactory **api lane** tests for REST or GraphQL services — asserting HTTP status codes, response schemas, headers, error contracts, pagination, idempotency and auth-token flows with httpx+pytest (Python) or supertest (TypeScript), validating responses against an OpenAPI spec or JSON Schema, and building consumer-driven contracts with Pact so a provider can never silently break its consumers.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# API and Contract Testing

TFactory's **api** lane exercises a running service over the wire instead of importing its code. It hits the `http` target declared in `.tfactory.yml` (a `base_url` plus a `health_check` path), asserts the response *contract* (status, body shape, headers), and — where a provider serves many consumers — pins that contract with Pact. This skill covers black-box endpoint testing in Python (`httpx` + `pytest`) and TypeScript (`supertest`), schema validation against OpenAPI/JSON-Schema, and consumer-driven contract testing.

---

## When to use this skill
- Generating tests for a REST or GraphQL endpoint added or changed on the feature branch (the api lane).
- Asserting a response matches its OpenAPI operation or a JSON Schema.
- Verifying error contracts (4xx/5xx bodies), pagination, idempotency keys, rate-limit headers, and auth-token handling.
- Writing a consumer-driven Pact contract, or verifying a provider against published pacts.
- Do NOT trigger for: pure-function unit tests (unit lane), browser UI flows (browser lane), spinning up real databases/brokers (integration lane — use `integration-testing-testcontainers`), or SAST/DAST security scanning (out of scope — delegated to dedicated pipelines).

---

## Key principles
1. **Test the contract, not the implementation** — assert status, body shape and headers a consumer relies on. Internal refactors must not break the test; a contract change must.
2. **Status code first, then body** — a wrong status (500 vs 200) makes body assertions noise. Assert `resp.status_code` before parsing JSON, and fail loudly with the body in the message.
3. **Validate shape with a schema, assert values selectively** — let JSON Schema / OpenAPI check structure and types; assert only the specific field values the AC pins. This keeps tests resilient to additive (non-breaking) fields.
4. **Every request is authenticated like production** — acquire a token the way a real client does, send it on every call, and add one negative test proving missing/expired tokens are rejected.
5. **Idempotent operations must prove idempotency** — a retried `PUT`/`DELETE` or a repeated `POST` with the same idempotency key must converge to one result, not duplicate it.
6. **Pagination is a contract** — test first page, a middle page, the last page, and past-the-end. Assert the cursor/next-link round-trips and that the full set is covered with no gaps or dupes.
7. **Pin cross-service expectations with consumer-driven contracts** — the consumer writes the pact; the provider verifies against it in CI. This catches breaking changes before deploy, not in prod.

---

## Core concepts
**api lane target** — `.tfactory.yml` declares an `http` target with `base_url` and `health_check`. TFactory gates on the health check, then runs api-lane tests against `base_url`. Tests read the base URL from env (`TFACTORY_API_BASE_URL`) so the same suite runs locally, in CI, and against staging.

**httpx + pytest (Python)** — `httpx.Client`/`AsyncClient` is a requests-compatible client with HTTP/2, timeouts and an ASGI transport (test an app in-process with no network). Pair with `pytest` fixtures for the client and auth token.

**supertest (TypeScript)** — wraps a Node HTTP server (or a `base_url`) with a fluent assertion API (`.expect(200)`, `.expect('Content-Type', /json/)`). Runs under Jest in the api lane for TS subtasks.

**OpenAPI / JSON Schema validation** — the service's OpenAPI doc yields per-operation response schemas. `jsonschema` (Python) or `ajv` (TS) validates a body against the schema for the path+method+status, catching missing/renamed/retyped fields automatically.

**Consumer-driven contract (Pact)** — the consumer defines expected interactions; `pact` records them as a pact file. The provider replays each interaction against its real handlers (`provider_states` set up data). A pact broker shares contracts and gates deploys via `can-i-deploy`.

**Error contract** — the documented shape of failure responses (e.g. RFC 7807 `application/problem+json` with `type`/`title`/`status`/`detail`). Errors are part of the API and get tested like success paths.

---

## Common tasks

### Test a REST endpoint with httpx + pytest
Read the base URL from env, share a client via a fixture, assert status then body.

```python
import os
import httpx
import pytest

BASE_URL = os.environ.get("TFACTORY_API_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def test_get_order_returns_expected_contract(client, auth_headers):
    resp = client.get("/orders/42", headers=auth_headers)
    # Status first — surface the body if it's wrong.
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")

    body = resp.json()
    # Assert only the fields the acceptance criteria pin.
    assert body["id"] == 42
    assert body["status"] in {"pending", "paid", "shipped"}
    assert isinstance(body["line_items"], list)
```

### Acquire and reuse an auth token
Fetch once per session; send on every request. Add a negative test.

```python
@pytest.fixture(scope="session")
def auth_headers(client):
    resp = client.post("/auth/token", json={
        "client_id": os.environ["TF_CLIENT_ID"],
        "client_secret": os.environ["TF_CLIENT_SECRET"],
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_missing_token_is_rejected(client):
    resp = client.get("/orders/42")  # no Authorization header
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/unauthorized")
```

### Validate a response against the OpenAPI / JSON schema
Extract the schema for the operation, validate the body — structure for free.

```python
import json
import jsonschema  # pip install jsonschema

def _schema_for(spec: dict, path: str, method: str, status: str) -> dict:
    op = spec["paths"][path][method.lower()]
    content = op["responses"][status]["content"]["application/json"]["schema"]
    # Inline $ref resolution against components for self-contained validation.
    return {**content, "components": spec.get("components", {})}


def test_order_matches_openapi(client, auth_headers):
    spec = json.loads(open("openapi.json").read())
    resp = client.get("/orders/42", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    schema = _schema_for(spec, "/orders/{id}", "get", "200")
    # Raises ValidationError listing the exact failing field/path.
    jsonschema.validate(instance=resp.json(), schema=schema)
```

### Test pagination round-trips with no gaps
Walk every page via the cursor; assert the union is complete and unique.

```python
def test_pagination_covers_all_orders(client, auth_headers):
    seen, cursor = [], None
    for _ in range(100):  # hard cap guards against an infinite next-link
        params = {"limit": 25, **({"cursor": cursor} if cursor else {})}
        resp = client.get("/orders", params=params, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        seen.extend(item["id"] for item in page["data"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert len(seen) == len(set(seen)), "duplicate ids across pages"
    assert seen == sorted(seen), "ordering contract violated"
```

### Prove idempotency on a POST
Same idempotency key → one resource, identical response.

```python
def test_create_payment_is_idempotent(client, auth_headers):
    key = "idem-7f3a"
    headers = {**auth_headers, "Idempotency-Key": key}
    payload = {"order_id": 42, "amount_cents": 1500}

    first = client.post("/payments", json=payload, headers=headers)
    second = client.post("/payments", json=payload, headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code in (200, 201)  # replay may be 200
    assert first.json()["payment_id"] == second.json()["payment_id"]
```

### Test a REST endpoint with supertest (TypeScript)
The TS api lane uses supertest under Jest against the running app or `base_url`.

```typescript
import request from 'supertest';

const baseUrl = process.env.TFACTORY_API_BASE_URL ?? 'http://localhost:8000';

describe('GET /orders/:id', () => {
  let token: string;

  beforeAll(async () => {
    const res = await request(baseUrl)
      .post('/auth/token')
      .send({ client_id: process.env.TF_CLIENT_ID, client_secret: process.env.TF_CLIENT_SECRET });
    token = res.body.access_token;
  });

  it('returns the order contract', async () => {
    const res = await request(baseUrl)
      .get('/orders/42')
      .set('Authorization', `Bearer ${token}`)
      .expect('Content-Type', /json/)
      .expect(200);

    expect(res.body).toMatchObject({ id: 42 });
    expect(['pending', 'paid', 'shipped']).toContain(res.body.status);
  });

  it('rejects a missing token', () =>
    request(baseUrl).get('/orders/42').expect(401));
});
```

### Write a consumer-driven Pact contract
The consumer declares the interaction; the provider verifies it later.

```python
# consumer side (pact-python) — produces orders-api.json
from pact import Consumer, Provider

pact = Consumer("checkout-ui").has_pact_with(Provider("orders-api"), port=1234)

def test_consumer_expects_order():
    expected = {"id": 42, "status": "paid"}
    (pact
        .given("order 42 exists and is paid")     # provider_state
        .upon_receiving("a request for order 42")
        .with_request("get", "/orders/42")
        .will_respond_with(200, body=expected))

    with pact:
        resp = httpx.get("http://localhost:1234/orders/42")
        assert resp.json()["status"] == "paid"
# Provider runs `pact-verifier` against the published pact, using
# provider_states to seed "order 42 exists and is paid" before replay.
```

### Test a GraphQL operation
GraphQL is one POST to `/graphql`; assert `errors` is absent and `data` shape.

```python
def test_graphql_order_query(client, auth_headers):
    query = """query($id: ID!) { order(id: $id) { id status } }"""
    resp = client.post("/graphql", headers=auth_headers,
                       json={"query": query, "variables": {"id": "42"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body["errors"]   # GraphQL hides errors in 200s
    assert body["data"]["order"]["status"] == "paid"
```

---

## Gotchas
1. **GraphQL returns 200 on errors** — a failed GraphQL query still answers `200 OK` with an `errors` array. Asserting only `status_code == 200` passes a broken query. Always assert `"errors" not in body`.
2. **`resp.json()` throws on empty/HTML bodies** — a `204 No Content` or an HTML 502 from a proxy has no JSON. Guard with a status check first, or branch on `content-type`, before calling `.json()`.
3. **Reused tokens expire mid-suite** — a session-scoped token can outlive its TTL on a slow run. Either request short-lived tokens per test class or refresh on a 401 and retry once.
4. **Additive schema changes shouldn't fail tests, but `additionalProperties:false` makes them** — an OpenAPI schema with `additionalProperties: false` rejects new fields. For consumer-side validation, prefer schemas that tolerate extra keys so a provider adding a field doesn't break consumers.
5. **Pagination tests with a fixed dataset are flaky against shared envs** — if other runs mutate the data, page counts drift. Seed a dedicated dataset (api lane can pair with a fixture endpoint) or scope queries to a unique tag/owner created in the test.
6. **Pact `provider_states` must actually seed data** — a pact verifies nothing if the provider state handler is a no-op. The handler for "order 42 exists" must insert order 42, or verification passes against an empty DB and lies.
7. **Trailing-slash and base_url joining bugs** — `httpx.Client(base_url=".../api")` + `client.get("/orders")` resolves relative to host, dropping `/api`. Use `base_url=".../api/"` and `client.get("orders")`, or full paths consistently.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Asserting `status_code == 200` only, ignoring the body | Passes when the endpoint returns the wrong (but 200) payload | Assert status, then validate body shape against the schema and pin AC-critical values |
| Hardcoding `http://localhost:8000` in every test | Can't run against CI/staging; ties the suite to one host | Read `TFACTORY_API_BASE_URL` from env, default to localhost |
| Calling `resp.json()` before checking status | A 500/HTML error raises a confusing `JSONDecodeError`, hiding the real failure | Check status first and put `resp.text` in the assert message |
| Snapshotting the entire response body | Breaks on every additive field; brittle and high-churn | Validate structure with JSON Schema, assert only pinned fields |
| Testing the provider's code directly instead of a pact | Doesn't catch consumer-breaking changes; couples tests to internals | Consumer-driven Pact verified by the provider in CI |
| One giant test that creates, reads, updates, deletes in sequence | A failure mid-chain leaves dirty state and an unclear cause | One behavior per test; share setup via fixtures, isolate teardown |
| Skipping negative/auth tests because "the happy path works" | 401/403/422/error-contract regressions ship silently | Add explicit tests for missing token, invalid input, and the error body shape |
| Re-using a long-lived token without refresh | Token expiry turns the whole suite red intermittently | Request per-class tokens or refresh-and-retry once on 401 |
