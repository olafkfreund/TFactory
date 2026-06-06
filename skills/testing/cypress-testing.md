# cypress-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: cypress,e2e,browser,retry-ability,cy-intercept,fixtures,custom-commands,junit,flake-avoidance,browser-lane

---

# Cypress Testing

Use this skill when writing or reviewing Cypress end-to-end tests for TFactory's browser lane — covering the chained command/retry-ability model, network stubbing with cy.intercept, fixtures, custom commands, session-based login reuse, flake avoidance, and JUnit reporter wiring so the Evaluator can parse results. Also covers when Cypress is the right tool versus Playwright. Reach for this whenever a generated Cypress test targets TFACTORY_TARGET_URL and must stay deterministic under the Evaluator's 3× stability signal.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Cypress Testing

Cypress is an alternative driver for TFactory's `browser` lane (Playwright is the default). Its command-chain model has built-in retry-ability that, used correctly, yields deterministic tests; used incorrectly (mixing sync logic with async commands) it produces exactly the flakiness the Evaluator's 3× stability signal rejects.

Tests run against the app under test at `TFACTORY_TARGET_URL` and emit JUnit XML for the Executor/Evaluator to parse. This skill covers commands, retry-ability, network control, custom commands, flake avoidance, and reporter setup.

---

## When to use this skill
- Writing Cypress e2e tests for user flows in a repo that already uses Cypress.
- Stubbing/spying on network with `cy.intercept` and `cy.wait('@alias')`.
- Sharing login via `cy.session` and reusing fixtures.
- Authoring custom commands to DRY repeated flows.
- Wiring the JUnit reporter so TFactory can ingest results.
- Deciding whether Cypress or Playwright fits a given target.
- Do NOT trigger for: Playwright suites (use playwright-browser-testing), JS/TS unit tests (use jest-vitest-testing), or Python tests (use pytest-mastery).

---

## Key principles
1. **Commands are async and chained, not promises** — `cy.get(...)` enqueues; it doesn't return a value you can `const x =`. Work inside `.then()` or `.should()`, never with raw return values.
2. **Lean on retry-ability** — `cy.get(...).should(...)` retries the query+assertion until it passes or times out. This is the deterministic substitute for `cy.wait(ms)`.
3. **Never `cy.wait(<number>)`** — fixed waits are the dominant flake source; wait on an aliased route or an assertion instead.
4. **Control the network with cy.intercept** — stub responses to make tests hermetic; alias them and `cy.wait('@alias')` to synchronize on real events.
5. **Reuse login with cy.session** — caches cookies/localStorage across tests so login isn't re-run (and isn't a per-test flake point).
6. **Custom commands for repeated flows** — encapsulate login/setup once in `commands.js`; keep specs about behavior.
7. **Assert, don't just act** — every meaningful action should be followed by a `.should()` so the mutation/stability signals have something concrete.
8. **Emit JUnit** — configure the reporter so the Evaluator parses pass/fail per test id.

---

## Core concepts
**Command chain** — `cy.get('input').type('hi').should('have.value', 'hi')`. Each command queues and runs in order; the chain yields a "subject" to the next command.

**Retry-ability** — queries (`get`, `contains`, `find`) plus assertions (`should`, `and`) retry as a unit until they pass or hit `defaultCommandTimeout`. Only the *last* query before an assertion is retried — keep them adjacent.

**cy.intercept** — registers a network interceptor (stub, spy, or modify). Aliased with `.as('name')`, then awaited via `cy.wait('@name')`, which also exposes the request/response for assertions.

**Fixture** — static JSON under `cypress/fixtures/`, loaded with `cy.fixture('file')` — typically fed into an intercept's response body.

**Custom command** — `Cypress.Commands.add('login', ...)` extends the `cy.*` API; defined in `cypress/support/commands.js`.

**cy.session** — caches and restores browser session state keyed by an id, so login setup runs once and is restored on later tests.

---

## Common tasks

### Basic flow with retry-ability (no fixed waits)
```js
describe('search', () => {
  beforeEach(() => {
    cy.visit('/'); // baseUrl from config (TFACTORY_TARGET_URL)
  });

  it('shows results for a query', () => {
    cy.get('[data-cy=search-input]').type('laptop');
    cy.get('[data-cy=search-submit]').click();

    // Retries the query + assertion together until results render.
    cy.get('[data-cy=result]').should('have.length', 10);
    cy.contains('[data-cy=result]', 'Laptop Pro').should('be.visible');
  });
});
```

### Config: base URL from TFACTORY_TARGET_URL + JUnit reporter
```js
// cypress.config.js
const { defineConfig } = require('cypress');

module.exports = defineConfig({
  reporter: 'junit',
  reporterOptions: {
    mochaFile: 'results/junit-[hash].xml', // Executor/Evaluator parse these
    toConsole: true,
  },
  e2e: {
    baseUrl: process.env.TFACTORY_TARGET_URL || 'http://localhost:3000',
    defaultCommandTimeout: 6000,
    video: true,             // evidence for triage
    screenshotOnRunFailure: true,
    retries: { runMode: 0 }, // Evaluator owns stability re-runs
  },
});
```

### Network stubbing + waiting on the alias
```js
it('renders the cart from a stubbed API', () => {
  cy.intercept('GET', '**/api/cart', { fixture: 'cart.json' }).as('getCart');

  cy.visit('/cart');
  cy.wait('@getCart');         // deterministic sync on the real request

  cy.get('[data-cy=cart-item]').should('have.length', 1);
  cy.contains('Widget').should('be.visible');
});

it('asserts the outgoing request body', () => {
  cy.intercept('POST', '**/api/orders').as('placeOrder');
  cy.get('[data-cy=checkout]').click();
  cy.wait('@placeOrder').its('request.body').should('deep.include', { total: 100 });
});
```

### Fixtures
```json
// cypress/fixtures/cart.json
{ "items": [{ "id": 1, "name": "Widget", "qty": 2 }] }
```

### Custom command + session-based login reuse
```js
// cypress/support/commands.js
Cypress.Commands.add('login', (email, password) => {
  cy.session([email, password], () => {
    cy.visit('/login');
    cy.get('[data-cy=email]').type(email);
    cy.get('[data-cy=password]').type(password, { log: false });
    cy.get('[data-cy=submit]').click();
    cy.location('pathname').should('eq', '/dashboard'); // confirm login took
  });
});
```
```js
// a spec — login is restored from cache, not re-run
beforeEach(() => {
  cy.login(Cypress.env('TEST_USER'), Cypress.env('TEST_PASS'));
  cy.visit('/dashboard');
});

it('greets the user', () => {
  cy.contains('h1', 'Welcome back').should('be.visible');
});
```

### Working with command subjects correctly
```js
// WRONG: cy.get does not return a value
// const text = cy.get('h1').text();  // undefined-ish, will not work

// RIGHT: use .then() to access the resolved subject
cy.get('[data-cy=total]')
  .invoke('text')
  .then((text) => {
    const value = Number(text.replace('$', ''));
    expect(value).to.be.greaterThan(0);
  });
```

---

## Gotchas
1. **Treating commands as synchronous** — `const el = cy.get(...)` doesn't give an element; commands are queued. Fix: chain `.then()`/`.should()`.
2. **`cy.wait(2000)`** — arbitrary delay flakes under sandbox load and fails 3× stability. Fix: `cy.wait('@alias')` or a retrying `.should()`.
3. **Assertion not adjacent to its query** — only the last query before `.should()` retries; intervening commands break retry-ability. Fix: keep `cy.get(...).should(...)` together.
4. **Forgetting to gitignore session/auth artifacts vs committing fixtures** — fixtures are committed test data; cached credentials/secrets are not.
5. **Intercept registered after `cy.visit`** — misses the page's initial requests. Fix: set up `cy.intercept` before `cy.visit`.
6. **No JUnit reporter configured** — Evaluator can't parse results; the run looks empty. Fix: set `reporter: 'junit'` with a `mochaFile` glob.
7. **`cy.session` without a validation block** — a stale/invalid cached session silently breaks later tests. Fix: pass a `validate` callback or assert post-login state inside the setup.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `cy.wait(3000)` to let the page settle | Arbitrary; slow + flaky, fails stability | `cy.wait('@alias')` or retrying `.should()` |
| `const x = cy.get(...)` then use `x` | Commands are async/queued, not values | `cy.get(...).then((x) => ...)` |
| Re-logging in via UI in every test | Slow; login becomes a per-test flake point | `cy.session` cached custom command |
| Asserting only that a click happened | Mutants survive; weak signal | Follow each action with a `.should()` on the result |
| Hardcoded `http://localhost:3000` | Ignores `TFACTORY_TARGET_URL` | Use `baseUrl` + relative paths |
| Default spec/json reporter only | Evaluator can't ingest results | Configure JUnit reporter with `mochaFile` |
| Bumping `retries` to mask flakes | Hides nondeterminism the Evaluator catches | Fix the root race; keep `runMode` retries at 0 |
| Using Cypress for multi-tab/multi-origin flows | Cypress is single-tab/origin-limited | Use Playwright for those; Cypress for single-origin app flows |
