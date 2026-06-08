# integration-testing-testcontainers

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: integration,testcontainers,postgres,redis,kafka,docker-compose,health-check,seed,fixtures,reset

---

# Integration Testing with Testcontainers

Use this skill when writing or reviewing TFactory **integration lane** tests that spin up real dependencies — Postgres, Redis, Kafka, S3/MinIO — in throwaway Docker containers via testcontainers-python or testcontainers-node, gating on health before any test runs, seeding and resetting state between tests, and wiring the same flow to `.tfactory.yml` `docker_compose` / AppRuntime targets so the suite tests against real infrastructure instead of mocks.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Integration Testing with Testcontainers

TFactory's **integration** lane proves that code works against *real* dependencies — a real Postgres, a real Redis, a real Kafka broker — not stand-ins. Testcontainers boots each dependency in a disposable Docker container scoped to the test session, waits until it is genuinely ready (a `health_check`, not a sleep), and tears it down afterward. This skill covers spinning up deps in Python and Node, health-gating, seeding/resetting between tests, and connecting it to `.tfactory.yml` `docker_compose` targets.

---

## When to use this skill
- Generating integration-lane tests that need a real database, cache, broker, or object store.
- Replacing brittle mocks of a data layer with a real container so SQL, migrations and transactions are actually exercised.
- Bringing up a multi-service stack via `docker_compose` and health-gating before tests run.
- Seeding fixtures and resetting state so tests are isolated and re-runnable (TFactory re-runs each test 3× for stability).
- Do NOT trigger for: black-box HTTP contract tests against an already-running service (api lane — use `api-and-contract-testing`), pure-function unit tests (unit lane), browser flows (browser lane), or cloud posture/CSPM scanning (separate cloud-testing flow).

---

## Key principles
1. **Real dependencies over mocks for the data layer** — a mocked `repo.save()` can't catch a wrong column type, a missing migration, or a broken transaction. Boot the real thing.
2. **Gate on readiness, never on `sleep`** — a container's port opens before the service accepts queries. Wait on a real signal (a log line, a `SELECT 1`, a topic-list call) so tests don't race startup.
3. **Disposable and ephemeral** — each run gets a fresh container on a random host port. Never depend on a fixed port or pre-existing data; never leave a container running.
4. **Session-scope the container, function-scope the data** — booting Postgres per test is slow; boot once per session, but reset/seed rows per test so tests stay isolated.
5. **Reset between tests, don't just append** — truncate (or roll back a transaction) before each test. Leftover rows from a prior test make assertions order-dependent and 3×-stability re-runs flaky.
6. **Pin image tags** — `postgres:16.4`, not `postgres:latest`. A floating tag turns "works on my machine" into a non-reproducible build and breaks TFactory's deterministic re-runs.
7. **One Ryuk to clean up** — let Testcontainers' reaper (Ryuk) remove containers even if the test crashes; don't hand-roll teardown that a hard failure can skip.

---

## Core concepts
**Testcontainers** — a library that programmatically starts Docker containers for tests, exposes their mapped host ports, applies a wait strategy, and reaps them after. `testcontainers-python` and `testcontainers-node` are the two TFactory uses.

**Wait strategy / health gate** — the readiness signal a container must emit before tests run: a log regex (`database system is ready to accept connections`), a TCP port, or a custom probe (`SELECT 1`). This is the integration-lane equivalent of `.tfactory.yml`'s `health_check`.

**AppRuntime target** — TFactory's notion of the running system under test. For integration the runtime is the app plus its containerized deps; the `docker_compose` target in `.tfactory.yml` declares the compose file, and TFactory health-gates the whole stack before dispatching tests.

**Mapped (random) port** — Docker assigns a free host port to the container's internal port. Always read it back (`container.get_exposed_port(5432)` / `getMappedPort(5432)`); never assume the default.

**Seed / reset** — seed = insert the fixture rows a test needs; reset = remove all rows (TRUNCATE, `FLUSHALL`, delete topics) so the next test starts clean. Often a per-test fixture wrapping a transaction that rolls back.

**Session vs function scope** — pytest fixture scope. Containers are expensive → `scope="session"`. Data state is per-test → `scope="function"` reset fixtures.

---

## Common tasks

### Spin up Postgres and health-gate it (Python)
Boot once per session; the library's wait strategy gates readiness.

```python
import pytest
import psycopg
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def pg():
    # Pin the tag — never :latest. Testcontainers waits for readiness internally.
    with PostgresContainer("postgres:16.4") as container:
        yield container


@pytest.fixture(scope="session")
def pg_dsn(pg):
    # Read the *mapped* host port — never assume 5432 on the host.
    return (
        f"postgresql://{pg.username}:{pg.password}"
        f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture(scope="session", autouse=True)
def _migrate(pg_dsn):
    # Apply schema once; this is also a real readiness probe (SELECT 1 would work too).
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(open("schema.sql").read())
        conn.commit()
```

### Reset and seed per test
Truncate before each test, then insert exactly what the test needs.

```python
@pytest.fixture()
def db(pg_dsn):
    with psycopg.connect(pg_dsn) as conn:
        # Reset: clean slate so 3x-stability re-runs are deterministic.
        conn.execute("TRUNCATE orders, line_items RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn


def test_repository_persists_order(db):
    from app.repo import OrderRepo
    repo = OrderRepo(db)
    # Seed via the code under test — exercises real SQL + types.
    order_id = repo.create(customer="acme", amount_cents=1500)
    db.commit()

    fetched = repo.get(order_id)
    assert fetched.amount_cents == 1500
    assert fetched.status == "pending"   # DB default actually applied
```

### Faster isolation with a rollback-per-test transaction
Wrap each test in a transaction and roll it back — no TRUNCATE needed.

```python
@pytest.fixture()
def tx(pg_dsn):
    conn = psycopg.connect(pg_dsn)
    conn.autocommit = False
    try:
        yield conn          # test runs inside an uncommitted transaction
    finally:
        conn.rollback()     # discard everything the test did
        conn.close()
```

### Spin up Redis (Python)
A cache dep is the same pattern; assert real eviction/TTL behavior.

```python
import redis
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_client():
    with RedisContainer("redis:7.4-alpine") as rc:
        client = redis.Redis(
            host=rc.get_container_host_ip(),
            port=int(rc.get_exposed_port(6379)),
            decode_responses=True,
        )
        yield client


def test_cache_expires(redis_client):
    redis_client.flushall()                       # reset
    redis_client.set("k", "v", ex=1)
    assert redis_client.get("k") == "v"
    redis_client.delete("k")
    assert redis_client.get("k") is None
```

### Spin up Kafka and round-trip a message (Python)
Broker readiness = able to list topics, not just an open port.

```python
from testcontainers.kafka import KafkaContainer
from confluent_kafka import Producer, Consumer


def test_event_round_trips():
    with KafkaContainer("confluentinc/cp-kafka:7.6.0") as kafka:
        bootstrap = kafka.get_bootstrap_server()
        prod = Producer({"bootstrap.servers": bootstrap})
        prod.produce("orders", value=b'{"id":42}')
        prod.flush()

        cons = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "test", "auto.offset.reset": "earliest",
        })
        cons.subscribe(["orders"])
        msg = cons.poll(timeout=10.0)
        assert msg is not None and msg.error() is None
        assert b'"id":42' in msg.value()
        cons.close()
```

### Spin up Postgres in Node (testcontainers-node)
Same disposable-container flow under Jest for TS integration subtasks.

```typescript
import { PostgreSqlContainer, StartedPostgreSqlContainer } from '@testcontainers/postgresql';
import { Client } from 'pg';

let container: StartedPostgreSqlContainer;
let client: Client;

beforeAll(async () => {
  container = await new PostgreSqlContainer('postgres:16.4').start(); // waits for ready
  client = new Client({ connectionString: container.getConnectionUri() });
  await client.connect();
  await client.query(/* sql */ `CREATE TABLE orders (id serial primary key, amount int)`);
}, 60_000); // generous timeout — image pull can be slow on a cold cache

afterAll(async () => {
  await client.end();
  await container.stop();
});

beforeEach(() => client.query('TRUNCATE orders RESTART IDENTITY')); // reset

test('persists an order', async () => {
  await client.query('INSERT INTO orders (amount) VALUES ($1)', [1500]);
  const { rows } = await client.query('SELECT amount FROM orders');
  expect(rows[0].amount).toBe(1500);
});
```

### Health-gate a docker_compose target before tests
When `.tfactory.yml` declares a compose stack, bring it up and wait on the declared `health_check` before dispatching.

```python
from testcontainers.compose import DockerCompose

def test_against_compose_stack():
    # Mirrors .tfactory.yml docker_compose: wait on each service's health.
    with DockerCompose(".", compose_file_name="docker-compose.test.yml") as stack:
        host = stack.get_service_host("api", 8000)
        port = stack.get_service_port("api", 8000)
        # Block until the app's /health is green — the integration gate.
        stack.wait_for(f"http://{host}:{port}/health")
        # ... run integration assertions against the live stack ...
```

---

## Gotchas
1. **`sleep(5)` instead of a wait strategy** — the port opens before Postgres accepts queries; a fixed sleep is both too short (flaky) and too slow (wastes time). Use the library wait strategy or a `SELECT 1` probe loop.
2. **Assuming the default port on the host** — Docker maps the container's 5432 to a random host port. Hardcoding `localhost:5432` connects to nothing (or worse, your dev DB). Always read `get_exposed_port` / `getMappedPort`.
3. **Per-test container = unbearably slow** — booting Postgres for every test multiplies image-pull and init by your test count, and 3×-stability re-runs make it worse. Session-scope the container, function-scope the reset.
4. **State leaks between tests** — forgetting to TRUNCATE/rollback lets row counts accumulate, so a test passes alone but fails in the suite. Reset in a function-scoped fixture, before the test, not after.
5. **`:latest` image tags** — a floating tag means a CI run can silently pull a new major version and break, destroying reproducibility and TFactory's deterministic re-runs. Pin exact tags.
6. **No Docker in the sandbox / DinD** — the integration lane needs a Docker daemon. In restricted CI use a Docker-enabled runner or remote `DOCKER_HOST`; Testcontainers respects `DOCKER_HOST`/`TESTCONTAINERS_RYUK_DISABLED` for such environments.
7. **First-run image pull blows the test timeout** — a cold cache pulling `postgres:16.4` can take 30–60s. Set generous `beforeAll`/fixture timeouts and pre-pull images in a CI warm-up step.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Mocking the repository/DB in an "integration" test | Tests the mock, not real SQL/migrations/types; defeats the lane's purpose | Boot a real container with Testcontainers and exercise actual queries |
| `time.sleep()` to wait for the container | Races startup → flaky, or over-waits → slow; flagged by flake-lint | Use the library wait strategy or poll a real readiness probe |
| Connecting to `localhost:5432` | Hits a fixed port the container isn't on (or your dev DB) | Read the mapped host port from the started container |
| Booting a container per test function | Orders-of-magnitude slower, especially with 3× stability re-runs | Session-scope the container; function-scope data reset |
| Never resetting state between tests | Accumulated rows make tests order-dependent and non-rerunnable | TRUNCATE/`FLUSHALL` or rollback a transaction in a per-test fixture |
| Using `:latest` image tags | Non-reproducible; a new image version can break CI silently | Pin exact image tags (`postgres:16.4`) |
| Manual `container.stop()` only in the happy path | A test crash skips teardown, leaking containers | Use `with`/`afterAll` + Ryuk reaper so cleanup always runs |
| Sharing one container across unrelated test modules without reset | Cross-module data bleed causes spooky-action-at-a-distance failures | Scope containers per module/session and reset data per test |
