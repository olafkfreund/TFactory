# playwright-browser-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: playwright,browser,e2e,locators,auto-waiting,storagestate,visual-regression,network-mocking,trace,browser-lane

---

# Playwright Browser Testing

Use this skill when writing or reviewing Playwright (@playwright/test) browser tests for TFactory's browser lane — covering role-based locators, web-first auto-waiting assertions (never fixed timeouts), storageState login reuse via scaffold_auth_setup, trace/video/screenshot evidence, network request mocking with page.route, and toHaveScreenshot visual-regression assertions against committed baselines. Reach for this whenever a generated browser test targets TFACTORY_TARGET_URL and must stay deterministic under the Evaluator's 3× stability signal.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Playwright Browser Testing

Playwright drives TFactory's `browser` lane. Tests run headless in the Docker sandbox against the application under test at `TFACTORY_TARGET_URL` (injected from the target's `base_url` in `.tfactory.yml`). The Evaluator re-runs each test 3× for stability, so the single most important discipline is leaning on Playwright's auto-waiting instead of fixed sleeps.

This skill covers locators, web-first assertions, authenticated-session reuse, evidence capture, network mocking, and visual baselines.

---

## When to use this skill
- Writing browser end-to-end tests for user flows (login, checkout, forms, navigation).
- Reusing an authenticated session across tests via `storageState`.
- Mocking or stubbing backend responses so a UI test is hermetic.
- Adding visual-regression assertions (`toHaveScreenshot`) with baselines.
- Capturing trace/video/screenshot evidence for triage of a failed run.
- Fixing browser tests the Evaluator flagged flaky (sleeps, race conditions).
- Do NOT trigger for: Cypress suites (use cypress-testing), JS/TS unit tests (use jest-vitest-testing), or Python tests (use pytest-mastery).

---

## Key principles
1. **Never use fixed timeouts** — `page.waitForTimeout(2000)` is the #1 flake source the 3× stability signal punishes. Web-first assertions auto-retry until the condition holds or the test times out.
2. **Locate by role and accessible name** — `getByRole('button', { name: 'Submit' })` is resilient and a11y-aware; CSS/XPath selectors break on refactor.
3. **Assertions auto-wait; actions auto-wait** — `expect(locator).toBeVisible()` and `locator.click()` both poll for actionability. Trust them; don't pre-check.
4. **Reuse login state, don't re-login per test** — a one-time `storageState` setup (TFactory's `scaffold_auth_setup`) makes the suite fast and removes the login form as a per-test flake point.
5. **Make tests hermetic with network mocking** — stub volatile/slow backends via `page.route` so the run doesn't depend on live data.
6. **Evidence on failure, not always** — configure `trace: 'on-first-retry'`, `video`/`screenshot: 'retain-on-failure'` so triage has artifacts without bloating green runs.
7. **Visual baselines are committed and reviewed** — `toHaveScreenshot` compares against a stored PNG; baselines are intentional, regenerated explicitly, never blindly.
8. **Target the injected URL** — read the app's base from `TFACTORY_TARGET_URL`; never hardcode `localhost:3000`.

---

## Core concepts
**Locator** — a lazy, re-queried handle to element(s). It re-resolves on each action, which is what makes auto-waiting work. Prefer `getByRole`, `getByLabel`, `getByText`, `getByTestId`.

**Web-first assertion** — `expect(locator).toBeVisible()`, `.toHaveText()`, `.toHaveURL()` etc. auto-retry until they pass or hit the assertion timeout. This replaces manual waits entirely.

**storageState** — a JSON snapshot of cookies + localStorage. Saved once in a setup project, then loaded via `use: { storageState }` so every test starts logged in.

**page.route** — intercepts network requests matching a glob/regex; you fulfill with a stub, abort, or continue. Makes the UI test independent of the backend.

**Trace** — a zip of DOM snapshots, network, console, and actions, openable in `npx playwright show-trace`. The primary triage artifact.

**toHaveScreenshot** — pixel-compares the page/element against a baseline PNG stored next to the test; mismatches above threshold fail. Baselines live under `__screenshots__`/snapshot dirs.

---

## Common tasks

### Role-based locators + web-first assertions
```ts
import { test, expect } from '@playwright/test';

test('user can search and see results', async ({ page }) => {
  await page.goto('/'); // baseURL comes from config (TFACTORY_TARGET_URL)

  await page.getByRole('searchbox', { name: 'Search products' }).fill('laptop');
  await page.getByRole('button', { name: 'Search' }).click();

  // Auto-waits for the list to render — no sleep, no manual wait.
  await expect(page.getByRole('list', { name: 'Results' })).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(10);
});
```

### Config: base URL from TFACTORY_TARGET_URL + evidence
```ts
// playwright.config.ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  use: {
    baseURL: process.env.TFACTORY_TARGET_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  expect: { timeout: 5_000 },
  retries: 0, // TFactory's Evaluator owns stability re-runs; keep 0 here
});
```

### storageState login reuse (TFactory scaffold_auth_setup pattern)
```ts
// auth.setup.ts — runs once, produces the storage state file
import { test as setup, expect } from '@playwright/test';
const authFile = 'playwright/.auth/user.json';

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(process.env.TFACTORY_TEST_USER!);
  await page.getByLabel('Password').fill(process.env.TFACTORY_TEST_PASS!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await page.context().storageState({ path: authFile });
});
```
```ts
// playwright.config.ts — wire setup as a dependency, reuse state in tests
projects: [
  { name: 'setup', testMatch: /auth\.setup\.ts/ },
  {
    name: 'chromium',
    dependencies: ['setup'],
    use: { storageState: 'playwright/.auth/user.json' },
  },
],
```

### Network mocking for a hermetic test
```ts
test('renders the cart from a stubbed API', async ({ page }) => {
  await page.route('**/api/cart', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [{ id: 1, name: 'Widget', qty: 2 }] }),
    });
  });

  await page.goto('/cart');
  await expect(page.getByText('Widget')).toBeVisible();
  await expect(page.getByRole('spinbutton', { name: 'Quantity' })).toHaveValue('2');
});
```

### Visual regression with toHaveScreenshot
```ts
test('pricing page matches the visual baseline', async ({ page }) => {
  await page.goto('/pricing');
  await expect(page.getByRole('main')).toBeVisible(); // settle before snapshot
  // Mask volatile regions; cap tolerance so anti-aliasing noise doesn't fail.
  await expect(page).toHaveScreenshot('pricing.png', {
    maxDiffPixelRatio: 0.01,
    mask: [page.getByTestId('live-timestamp')],
    animations: 'disabled',
  });
});
// First run / intentional change: regenerate with `--update-snapshots`.
```

### Waiting for a specific response (not a sleep)
```ts
test('submits and confirms', async ({ page }) => {
  await page.goto('/contact');
  const respPromise = page.waitForResponse((r) => r.url().includes('/api/contact') && r.ok());
  await page.getByRole('button', { name: 'Send' }).click();
  await respPromise; // deterministic synchronization on the real event
  await expect(page.getByRole('alert')).toHaveText('Message sent');
});
```

---

## Gotchas
1. **`waitForTimeout` for synchronization** — passes locally, flakes in the sandbox under load, fails the 3× stability run. Fix: assert on the post-condition (`toBeVisible`, `waitForResponse`).
2. **Strict-mode locator resolving to many nodes** — `getByRole('button')` throws if it matches multiple. Fix: add `{ name: ... }` or `.first()` only when genuinely ambiguous.
3. **storageState path not gitignored vs baseline committed** — auth state is a secret (gitignore it); screenshot baselines are committed. Don't swap these.
4. **Animations causing screenshot diffs** — CSS transitions produce pixel noise. Fix: `animations: 'disabled'` and a small `maxDiffPixelRatio`.
5. **Hardcoded `localhost` URLs** — ignores `TFACTORY_TARGET_URL`, tests the wrong target. Fix: rely on `baseURL` + relative paths.
6. **Mocking after navigation** — `page.route` registered after `goto` misses the initial requests. Fix: register routes before navigating.
7. **Baselines generated on a different OS/browser** — font rendering differs across platforms; diffs appear spuriously. Fix: generate baselines in the same container the sandbox uses.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `await page.waitForTimeout(3000)` | Arbitrary wait → slow + flaky, fails stability | Web-first assertion or `waitForResponse` on the real event |
| `page.locator('.submit-btn')` | Brittle CSS coupling, ignores a11y | `getByRole('button', { name: /submit/i })` |
| Logging in inside every test | Slow; login form becomes a per-test flake point | One `storageState` setup, reuse via `dependencies` |
| `if (await el.isVisible()) await el.click()` | Race between check and act | Just `await el.click()` — it auto-waits for actionability |
| Committing the auth `storageState` JSON | Leaks session secrets | Gitignore it; generate fresh in the setup project |
| `toHaveScreenshot` with no mask/threshold | Timestamps/animations cause spurious diffs | Mask volatile regions, set `maxDiffPixelRatio` |
| Setting `retries` high to "fix" flakes | Hides nondeterminism the Evaluator will catch anyway | Make the test deterministic; keep retries at 0 |
| `page.$('selector')` (ElementHandle) | Snapshot handle, no auto-wait, deprecated style | Use `Locator` (`page.getByRole`/`locator`) |
