# jest-vitest-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: javascript,typescript,jest,vitest,testing-library,mocks,fake-timers,async,coverage,unit-lane

---

# Jest & Vitest Testing

Use this skill when writing or reviewing JavaScript/TypeScript unit tests for TFactory's unit lane — covering describe/it structure, mocking with jest.fn/jest.mock and vi.mock, fake timers, async/await assertions, @testing-library role-based queries (getByRole), coverage configuration, and the concrete differences between Jest and Vitest (config, globals, mock API, ESM). Reach for this whenever a generated TS/JS test must be deterministic enough to pass the Evaluator's 3× stability and mutation (Stryker) signals.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Jest & Vitest Testing

Jest and Vitest power TFactory's `unit` lane for TypeScript/JavaScript. Vitest is the modern, Vite-native, ESM-first runner with a near-identical API; Jest remains the incumbent for many CRA/Next/Node projects. The Evaluator runs each test 3× for stability and uses Stryker for TS mutation, so determinism and meaningful assertions matter as much as passing.

This skill covers test structure, the mock APIs of both runners, fake timers, async assertions, Testing Library queries, and the migration-shaped differences between the two.

---

## When to use this skill
- Writing unit tests for TS/JS functions, classes, hooks, or React components.
- Choosing or configuring Jest vs Vitest for a project's unit lane.
- Mocking modules, functions, timers, or fetch deterministically.
- Querying rendered components by accessible role/label rather than test ids.
- Fixing tests the Evaluator flagged flaky (timers, microtask ordering, real fetch).
- Do NOT trigger for: Python tests (use pytest-mastery), full-browser end-to-end flows (use playwright-browser-testing), or Cypress suites (use cypress-testing).

---

## Key principles
1. **Same API, different engine** — Vitest mirrors Jest's `describe/it/expect`; the main swaps are `jest.*` → `vi.*` and config in `vite.config.ts` vs `jest.config.js`. Know which runner the repo uses before writing.
2. **Determinism via fake timers** — never await real wall-clock delays; install fake timers and advance them so 3× stability never flips.
3. **Query by role, not by implementation** — `getByRole('button', { name: /save/i })` survives refactors and asserts accessibility; `querySelector('.btn')` does not.
4. **Mock at the module boundary** — `jest.mock('./api')` / `vi.mock('./api')` is hoisted; mock the dependency, test your unit's logic.
5. **Await everything async** — use `findBy*`, `await waitFor`, and `await expect(p).rejects` so assertions don't run before the promise settles.
6. **Reset mocks between tests** — `clearMocks`/`resetMocks` (or `restoreAllMocks`) prevents call-count bleed across tests.
7. **Assert behavior, not snapshots-of-everything** — broad snapshots survive mutants and rot; targeted assertions kill mutants and read clearly.
8. **ESM is a first-class concern in Vitest** — `vi.mock` factories run hoisted; reach for `vi.importActual` to keep the real parts you don't mock.

---

## Core concepts
**describe / it (test)** — `describe` groups, `it`/`test` is a single case. Keep one behavior per `it`.

**Mock function** — `jest.fn()` / `vi.fn()` records calls and lets you stub return values (`mockReturnValue`, `mockResolvedValue`). Assert with `expect(fn).toHaveBeenCalledWith(...)`.

**Module mock** — `jest.mock('./mod')` / `vi.mock('./mod')` replaces a whole module; the call is *hoisted* above imports, so factory functions must be self-contained.

**Fake timers** — `jest.useFakeTimers()` / `vi.useFakeTimers()` replace `setTimeout`, `setInterval`, `Date`; advance with `jest.advanceTimersByTime(ms)` / `vi.advanceTimersByTimeAsync(ms)`.

**Testing Library queries** — `getBy*` (throws if absent), `queryBy*` (returns null), `findBy*` (async, retries). Prefer role/label/text queries that mirror how users perceive the UI.

**Vitest globals** — off by default; either set `test.globals: true` in config or `import { describe, it, expect, vi } from 'vitest'`.

---

## Common tasks

### describe/it with mocked functions (Jest)
```ts
import { calculateTotal } from './cart';

describe('calculateTotal', () => {
  it('applies a discount callback to the subtotal', () => {
    const discount = jest.fn((subtotal: number) => subtotal * 0.9);

    const total = calculateTotal([{ price: 50 }, { price: 50 }], discount);

    expect(discount).toHaveBeenCalledWith(100);
    expect(total).toBe(90);
  });
});
```

### Module mock + async (Vitest)
```ts
import { describe, it, expect, vi } from 'vitest';
import { getUserName } from './user';

vi.mock('./api', () => ({
  fetchUser: vi.fn().mockResolvedValue({ id: 1, name: 'Ada' }),
}));

describe('getUserName', () => {
  it('returns the name from the API', async () => {
    await expect(getUserName(1)).resolves.toBe('Ada');
  });
});
```

### Partial mock with importActual (Vitest)
```ts
import { describe, it, expect, vi } from 'vitest';

vi.mock('./config', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./config')>();
  return { ...actual, FEATURE_FLAG: true }; // keep the rest real
});
```

### Fake timers (deterministic debounce test)
```ts
import { describe, it, expect, vi, afterEach } from 'vitest';
import { debounce } from './debounce';

afterEach(() => vi.useRealTimers());

describe('debounce', () => {
  it('calls once after the wait window', () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = debounce(fn, 200);

    debounced(); debounced(); debounced();
    vi.advanceTimersByTime(200);

    expect(fn).toHaveBeenCalledTimes(1); // never await real 200ms
  });
});
```

### React component via Testing Library (role-based queries)
```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Counter } from './Counter';

it('increments when the button is clicked', async () => {
  const user = userEvent.setup();
  render(<Counter />);

  await user.click(screen.getByRole('button', { name: /increment/i }));

  expect(screen.getByRole('status')).toHaveTextContent('1');
});

it('shows the async result after loading', async () => {
  render(<Profile id={1} />);
  // findBy* retries until it appears — no fixed timeout
  expect(await screen.findByRole('heading', { name: /ada/i })).toBeInTheDocument();
});
```

### Coverage configuration
```ts
// vitest.config.ts
import { defineConfig } from 'vitest/config';
export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test/setup.ts'], // e.g. @testing-library/jest-dom
    coverage: { provider: 'v8', reporter: ['text', 'lcov'], all: true },
  },
});
```
```js
// jest.config.js
module.exports = {
  testEnvironment: 'jsdom',
  collectCoverage: true,
  coverageReporters: ['text', 'lcov'],
  clearMocks: true,
  setupFilesAfterEnv: ['<rootDir>/test/setup.ts'],
};
```

---

## Gotchas
1. **`vi.mock`/`jest.mock` hoisting** — the call is lifted above imports, so its factory can't reference outer-scope variables not prefixed with `mock`. Fix: define stubs inside the factory or use `vi.hoisted`.
2. **Forgetting to await `findBy`/`waitFor`** — assertion runs before the DOM updates → intermittent failures across the 3× run. Fix: `await screen.findBy*`.
3. **Fake timers left installed** — leaks into later tests, breaking unrelated `setTimeout`s. Fix: `afterEach(() => vi.useRealTimers())`.
4. **`Date.now()` without faking** — non-deterministic; mutation/stability punish it. Fix: `vi.setSystemTime(new Date('2026-01-01'))` under fake timers.
5. **Vitest globals not enabled** — `describe is not defined` when `globals` is false and nothing imported. Fix: import from `'vitest'` or set `globals: true`.
6. **Mock state bleeding between tests** — call counts accumulate without reset. Fix: `clearMocks: true` (Jest) / `clearMocks: true` in Vitest config, or `vi.clearAllMocks()`.
7. **`getByRole` not finding an accessible name** — element lacks a label/text so the accessible name is empty. Fix: add `aria-label`/visible text, or assert the missing a11y is the real bug.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `await new Promise(r => setTimeout(r, 500))` | Real delay → slow + flaky across 3× stability | Fake timers + `advanceTimersByTime` |
| `container.querySelector('.save-btn')` | Couples test to CSS; breaks on refactor; ignores a11y | `getByRole('button', { name: /save/i })` |
| `toMatchSnapshot()` on a whole tree | Snapshots survive mutants and rot silently | Assert specific text/roles/values |
| Calling real `fetch` in a unit test | Network is unavailable/nondeterministic in sandbox | Mock the module or `vi.stubGlobal('fetch', fn)` |
| `jest.fn()` referenced inside `jest.mock` factory | Hoisting error: var used before init | Prefix with `mock` or use `vi.hoisted` |
| Not awaiting a rejected promise assertion | Unhandled rejection / false pass | `await expect(p).rejects.toThrow(...)` |
| Mixing `jest.*` calls in a Vitest project | `jest is not defined` | Use `vi.*`; both share `expect`/`describe` |
| `getByText` for interactive elements | Misses role semantics; ambiguous matches | Prefer `getByRole` with the element's role |
