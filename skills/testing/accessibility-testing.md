# accessibility-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: accessibility,a11y,axe-core,playwright,wcag,aria,keyboard-nav,contrast,browser-lane

---

# Accessibility Testing

Use this skill when you need to add or interpret accessibility (a11y) checks in TFactory's browser lane — running axe-core via @axe-core/playwright, mapping violations to WCAG success criteria, asserting on accessible role/name/contrast, verifying keyboard navigation and focus order, and triaging axe violations into accept/flag/reject. Covers what automated a11y testing can and cannot catch.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Accessibility Testing

Accessibility testing in TFactory rides the browser lane (Playwright): every page or component the browser lane exercises can also be scanned with axe-core for WCAG violations. Automated a11y scanning catches roughly a third of WCAG issues deterministically — missing labels, contrast failures, broken ARIA — and that third is exactly the part worth gating in CI. This skill covers wiring axe into Playwright tests, reading violations against WCAG criteria, testing keyboard nav, and triaging results.

---

## When to use this skill
- Adding an axe-core scan to a Playwright (browser-lane) test.
- Mapping an axe violation to its WCAG success criterion and severity.
- Asserting that an element has an accessible name/role (not just visible text).
- Verifying keyboard navigation, focus order, and focus visibility.
- Triaging a batch of axe violations into accept / flag / reject.
- Do NOT trigger for: non-UI tests (api/unit/integration lanes), performance (performance-and-load-testing), cloud posture (cloud-posture-testing), or claiming full WCAG compliance from automation alone (much of WCAG needs manual/AT testing).

---

## Key principles
1. **Accessible name beats visible text** — assert what assistive tech announces (role + accessible name via `getByRole`), not the raw DOM text. A button with an icon and no label is broken even if it "looks" labeled.
2. **Automation covers ~a third of WCAG** — axe finds programmatically-detectable issues (contrast, labels, ARIA validity, landmark structure). It cannot judge whether alt text is *meaningful* or whether reading order makes sense. Don't claim compliance from a clean axe run.
3. **Gate on a WCAG conformance level** — pick a target (commonly WCAG 2.1 AA) and configure axe's `tags` to that level so you're testing against an explicit bar, not "whatever axe defaults to".
4. **Keyboard is a first-class path** — every interactive element must be reachable and operable by keyboard alone, with a visible focus indicator and logical tab order. axe doesn't fully verify this; test it explicitly.
5. **Scan the rendered state, not the initial load** — a11y violations hide in modals, expanded menus, and error states. Drive the UI into each state, then scan.
6. **Severity ≠ WCAG level** — axe's impact (critical/serious/moderate/minor) is an axe heuristic; the WCAG criterion and conformance level decide whether it's a hard fail. Triage on both.
7. **Fix at the source, don't suppress** — disabling an axe rule to go green hides a real barrier for users. Suppress only with a documented, justified reason.
8. **Scope, don't disable, for third-party widgets** — when a violation is in code you can't change, `.exclude()` that region so the rule keeps protecting your own markup, rather than turning the rule off globally.
9. **a11y is part of the browser lane, not a bolt-on** — fold axe scans into existing Playwright tests so accessibility coverage grows with UI coverage and flows through the same Evaluator verdict.

---

## Core concepts
**axe-core** — the accessibility rules engine. Each rule maps to one or more WCAG success criteria and reports violations with an impact level, the failing nodes, and remediation guidance.

**@axe-core/playwright** — the integration that runs axe inside a Playwright page context and returns structured results — the natural fit for TFactory's browser lane.

**WCAG success criteria & levels** — the standard's checkpoints (e.g. 1.4.3 Contrast Minimum, 4.1.2 Name/Role/Value, 2.1.1 Keyboard). Levels A / AA / AAA set the conformance bar; AA is the common legal/practical target.

**Accessible role & name** — what AT exposes for an element: its role (button, link, textbox) and its accessible name (from label, aria-label, alt, etc.). `getByRole(role, { name })` asserts on exactly this.

**Color contrast** — ratio between text and background; WCAG 1.4.3 requires ≥ 4.5:1 for normal text (3:1 for large). axe checks this deterministically.

**Keyboard navigation & focus order** — tab sequence through interactive elements, presence of a visible focus ring, no keyboard traps, logical order matching visual order.

**Violation triage** — mapping each axe finding to accept (false positive / out of scope), flag (real but non-blocking), or reject (WCAG-AA hard fail) — the same accept/flag/reject vocabulary the Evaluator uses.

**Where a11y rides in TFactory** — accessibility checks are not a separate lane; they're assertions added inside the `browser` lane (Playwright). Any browser-lane subtask that drives a page can fold in an axe scan, so a11y coverage grows naturally with UI coverage. The Evaluator then judges the resulting test like any other browser test — its semantic_relevance asks "does this verify the AC's accessibility claim".

**The ~⅓ rule** — automated tooling reliably catches roughly a third of WCAG success criteria: the programmatically-detectable ones (contrast ratios, missing labels, invalid ARIA, missing landmarks, duplicate IDs). The other two-thirds — meaningful alt text, logical reading/focus order, error-recovery clarity, cognitive load — need a human with assistive technology. Treat axe as the automatable floor, never the ceiling.

**WCAG POUR principles** — the standard organizes criteria under Perceivable, Operable, Understandable, Robust. axe maps mostly to Perceivable (contrast, text alternatives) and Robust (valid ARIA/name-role-value); Operable (keyboard, focus) is partly automatable and partly manual; Understandable is largely manual. Knowing which principle a violation sits under tells you whether automation can even confirm the fix.

---

## Common tasks
### Add an axe scan to a Playwright test
```typescript
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('home page has no WCAG AA violations', async ({ page }) => {
  await page.goto('/');
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])  // explicit conformance bar
    .analyze();
  expect(results.violations).toEqual([]);
});
```

### Assert on accessible role + name
```typescript
// Verifies AT-exposed name, not just visible text
await expect(page.getByRole('button', { name: 'Submit order' })).toBeVisible();
```

### Scan a non-initial state (modal/error)
```typescript
await page.getByRole('button', { name: 'Open settings' }).click();
const results = await new AxeBuilder({ page })
  .include('[role="dialog"]')   // scan the opened modal specifically
  .withTags(['wcag21aa'])
  .analyze();
expect(results.violations).toEqual([]);
```

### Verify keyboard navigation and focus
```typescript
await page.keyboard.press('Tab');
await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
// Walk the tab order; assert each interactive element is reachable and focus is visible.
```

### Triage axe violations
For each violation: read the WCAG criterion + impact, decide reject (AA hard fail like missing form label or contrast < 4.5:1), flag (real but lower-priority, e.g. moderate landmark issue), or accept (documented false positive). Report counts by impact and by WCAG criterion.

### Exclude a known third-party region without disabling rules
When a violation lives in a vendor widget you can't fix, scope the scan rather than turning off the rule globally — so the rule still protects your own markup:
```typescript
const results = await new AxeBuilder({ page })
  .exclude('#third-party-chat-widget')   // narrow scope, keep the rule active
  .withTags(['wcag21aa'])
  .analyze();
expect(results.violations).toEqual([]);
```
Document the exclusion and track the vendor fix; never disable the rule itself.

### Assert a complete keyboard journey
Beyond a single focus check, walk the whole interactive path and confirm no trap:
```typescript
await page.keyboard.press('Tab'); // skip link
await page.keyboard.press('Tab'); // nav
await page.keyboard.press('Tab'); // primary CTA
await expect(page.getByRole('button', { name: 'Get started' })).toBeFocused();
await page.keyboard.press('Enter'); // operable by keyboard, not just click
await expect(page.getByRole('dialog')).toBeVisible();
await page.keyboard.press('Escape'); // can exit — no keyboard trap
await expect(page.getByRole('dialog')).toBeHidden();
```

### Report a11y results in the accept/flag/reject vocabulary
Summarize a scan the way the Evaluator would read it: count AA hard fails (→ reject candidates), real-but-lower-priority issues (→ flag), and documented false positives (→ accept). This keeps a11y findings legible alongside the rest of the triage report rather than as a separate raw violation dump.

### Map a violation to its WCAG criterion and decision
Each axe violation carries the criteria it fails; use that to decide, not the impact word alone:
| axe rule | WCAG criterion (level) | Typical decision |
|---|---|---|
| color-contrast | 1.4.3 Contrast Minimum (AA) | reject (AA hard fail) |
| label / input has no accessible name | 4.1.2 Name, Role, Value (A) | reject |
| image-alt missing | 1.1.1 Non-text Content (A) | reject |
| landmark-unique | 1.3.1 Info and Relationships (A) | flag |
| region (content not in a landmark) | 1.3.1 (A) | flag |
| meta-viewport (zoom disabled) | 1.4.4 Resize Text (AA) | reject |
Decisions assume a WCAG 2.1 AA target; raise the bar (more rejects) for AAA, lower it for A-only.

### Combine a11y with the functional browser assertion
Don't write a separate a11y-only test when you already drive the flow — fold the scan into the functional test so one Playwright test verifies behavior *and* accessibility:
```typescript
test('checkout form submits and is accessible', async ({ page }) => {
  await page.goto('/checkout');
  await page.getByRole('textbox', { name: 'Card number' }).fill('4242424242424242');
  await page.getByRole('button', { name: 'Pay' }).click();
  await expect(page.getByRole('status')).toHaveText(/payment confirmed/i); // behavior
  const a11y = await new AxeBuilder({ page }).withTags(['wcag21aa']).analyze();
  expect(a11y.violations).toEqual([]);                                       // accessibility
});
```

---

## Gotchas
1. **A clean axe run is not "accessible"** — automation misses meaningful-alt-text, logical reading order, and most cognitive criteria. A green scan is necessary, never sufficient; pair with manual/AT checks for claims of compliance.
2. **Scanning only the initial page misses most violations** — modals, dropdowns, toasts, and validation errors render later. Drive the UI into each state and scan it.
3. **`getByText` instead of `getByRole`** — asserting on visible text passes for an element AT can't perceive. Use `getByRole` with `name` to test the accessible name.
4. **Disabling rules to go green** — turning off a failing axe rule hides a real barrier. Only suppress with a written justification and a tracking issue.
5. **Confusing axe impact with WCAG level** — a "moderate" axe impact can still be an AA failure. Triage on the mapped WCAG criterion/level, not just axe's severity word.
6. **Forgetting keyboard traps & focus visibility** — axe won't catch a modal you can't Tab out of or a removed focus ring. Test keyboard operability explicitly.
7. **Default axe tags drift over versions** — without `.withTags(...)` you test against an implicit, shifting set. Pin the conformance tags you actually target.
8. **Counting raw violations instead of triaging** — "37 violations" is noise; the same DOM pattern repeated 30× is one issue. Triage by WCAG criterion + impact into accept/flag/reject, not by raw node count.
9. **Testing only happy-path screens** — error states, empty states, and loading states have their own a11y failures (unlabeled spinners, focus lost on error). Drive and scan those states too.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Claiming compliance from a clean axe run | Automation covers only ~⅓ of WCAG | Treat axe as a floor; add manual/AT testing for compliance claims |
| Asserting on `getByText` for interactive elements | Visible text ≠ accessible name AT announces | Use `getByRole(role, { name })` |
| Scanning only the initial page load | Modals/menus/errors hide violations | Drive each state, then scan it |
| Disabling a failing axe rule to go green | Hides a real barrier for disabled users | Fix at source; suppress only with documented justification |
| Treating axe impact as the WCAG level | Impact is an axe heuristic, not conformance | Map to the WCAG criterion + level to decide reject |
| Skipping keyboard-only testing | axe can't fully verify focus/traps/order | Test Tab order, focus visibility, no keyboard traps |
| Leaving axe tags unset | You test a drifting default rule set | Pin `.withTags([...])` to your target level (e.g. AA) |
| Reporting raw violation count only | Hides which are AA hard fails | Triage by WCAG criterion + impact into accept/flag/reject |
