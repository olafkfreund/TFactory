# visual-regression-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: visual-regression,playwright,toHaveScreenshot,baseline,masking,animations,threshold,browser,flake,cross-platform

---

# Visual Regression Testing

Use this skill when writing or reviewing TFactory **browser lane** visual-regression tests with Playwright's `toHaveScreenshot` — capturing, accepting and updating baselines, masking dynamic regions (clocks, avatars, ads), disabling animations and hiding the caret to kill flake, tuning `maxDiffPixelRatio`/`threshold`, and taming cross-platform rendering differences — wired to TFactory's per-target `visual_baseline` store and the `stage_baselines` step that loads baselines into the run.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Visual Regression Testing

Visual regression testing pins how the UI *looks*, not just how it behaves. Playwright's `toHaveScreenshot` captures a screenshot, diffs it pixel-by-pixel against a stored baseline, and fails when the rendered output drifts. In TFactory these live in the **browser** lane: baselines are stored per-target in the `visual_baseline` store, and a `stage_baselines` step loads the right baseline set into each run before the test executes. This skill covers the capture/accept/update workflow, killing flake (animations, carets, dynamic regions), threshold tuning, and cross-platform rendering pitfalls.

---

## When to use this skill
- Generating browser-lane tests that assert a page/component renders pixel-correct against a baseline.
- Establishing, accepting, or intentionally updating visual baselines after a deliberate UI change.
- Masking dynamic regions and stabilizing the page (animations, fonts, caret, network) so diffs are deterministic.
- Tuning `maxDiffPixelRatio` / `threshold` to absorb sub-pixel rendering noise without hiding real regressions.
- Debugging cross-platform screenshot flake (CI Linux vs dev macOS font/AA differences).
- Do NOT trigger for: behavioral E2E assertions without a screenshot (regular browser-lane interaction tests), API contract tests (api lane), unit tests, or Java/PIT mutation work.

---

## Key principles
1. **A baseline is a contract, and updates are deliberate** — `--update-snapshots` must be a conscious, reviewed act tied to an intended UI change, never a reflex to make red turn green. An accidental update bakes the bug into the baseline.
2. **Stabilize before you snapshot** — wait for fonts, network idle, and the element to settle. A screenshot taken mid-render diffs against nothing meaningful and flakes.
3. **Disable animations and hide the caret** — moving pixels are the #1 source of visual flake. `animations: 'disabled'` and `caret: 'hide'` are mandatory, not optional.
4. **Mask what you can't control** — clocks, avatars, ad slots, randomized IDs, live counters. Mask these regions so legitimate dynamic content doesn't trigger false positives.
5. **Pin the viewport, device-scale, and color scheme** — screenshots are resolution- and DPR-dependent. Fix viewport size and `deviceScaleFactor` so a baseline captured in one config isn't compared against another.
6. **Generate baselines on the same platform that diffs them** — macOS and Linux render fonts/anti-aliasing differently. Baselines must come from the CI/runner OS (TFactory's `visual_baseline` is keyed per-target), or every diff is noise.
7. **Scope the snapshot tightly** — prefer `locator.toHaveScreenshot()` on the component over a full-page shot. A smaller surface means fewer unrelated regions can flake the test.

---

## Core concepts
**`toHaveScreenshot`** — Playwright's web-first visual assertion. On first run it writes a baseline; on later runs it captures, diffs against the baseline, and retries until match-or-timeout. Failures emit `actual`, `expected`, and `diff` PNGs into the test-results dir.

**Baseline (snapshot)** — the reference PNG, named by test + project + platform (e.g. `home-page-chromium-linux.png`). Playwright stores these next to the spec by default; TFactory instead sources them from the per-target `visual_baseline` store.

**`visual_baseline` store + `stage_baselines`** — TFactory keeps accepted baselines per test-target (so a `docker_compose` target and a `kubernetes` port-forward target each have their own set). The `stage_baselines` run step copies the target's baseline set into the test's snapshot directory before Playwright runs, so the diff is against the approved reference.

**`maxDiffPixelRatio` / `maxDiffPixels` / `threshold`** — tolerance knobs. `threshold` (0–1, default ~0.2) is the per-pixel YIQ color-distance below which two pixels count as equal. `maxDiffPixels`/`maxDiffPixelRatio` cap how many pixels may differ before the assertion fails.

**Masking** — `mask: [locator, ...]` paints opaque boxes over regions before capture, so dynamic content there is excluded from the diff.

**`animations: 'disabled'` / `caret: 'hide'`** — capture options that freeze CSS animations/transitions to their end state and hide the blinking text caret — removing the two most common moving-pixel flake sources.

---

## Common tasks

### Capture and assert a component baseline
First run writes the baseline; later runs diff against it.

```typescript
import { test, expect } from '@playwright/test';

test('order summary renders correctly', async ({ page }) => {
  await page.goto('/orders/42');
  // Stabilize: wait for the element AND for fonts/layout to settle.
  const summary = page.getByTestId('order-summary');
  await expect(summary).toBeVisible();
  await page.evaluate(() => document.fonts.ready);

  // Scope to the component; freeze motion + caret.
  await expect(summary).toHaveScreenshot('order-summary.png', {
    animations: 'disabled',
    caret: 'hide',
  });
});
```

### Accept / update baselines deliberately
Run only the affected tests with the update flag — never blanket-update.

```bash
# Create or refresh a baseline for ONE spec, on the CI/runner platform.
npx playwright test order-summary.spec.ts --update-snapshots

# In TFactory, the accepted PNGs are promoted into the target's
# visual_baseline store so stage_baselines feeds them to future runs.
# Review the new PNG in the PR before promoting — an update is a contract change.
```

### Mask dynamic regions
Exclude clocks, avatars, ads, randomized content from the diff.

```typescript
test('dashboard layout is stable', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page.getByTestId('dashboard')).toHaveScreenshot('dashboard.png', {
    animations: 'disabled',
    caret: 'hide',
    mask: [
      page.getByTestId('live-clock'),     // changes every second
      page.getByTestId('user-avatar'),    // varies per user
      page.locator('.ad-slot'),           // third-party, non-deterministic
    ],
    // Tolerate AA noise without hiding real diffs.
    maxDiffPixelRatio: 0.01,
  });
});
```

### Pin viewport, DPR, and color scheme for determinism
Lock rendering config in the Playwright project so baselines are comparable.

```typescript
// playwright.config.ts — the browser-lane project
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,   // global tolerance for sub-pixel noise
      animations: 'disabled',    // applied to every screenshot assertion
      caret: 'hide',
    },
  },
  projects: [{
    name: 'chromium',
    use: {
      ...devices['Desktop Chrome'],
      viewport: { width: 1280, height: 720 },
      deviceScaleFactor: 1,        // DPR pinned — retina shots won't compare to 1x
      colorScheme: 'light',        // theme pinned
    },
  }],
});
```

### Stop content/network flake before the shot
Wait for the page to truly settle; neutralize non-deterministic data.

```typescript
test('product grid is pixel-stable', async ({ page }) => {
  // Freeze time so any rendered timestamps are constant.
  await page.clock.setFixedTime(new Date('2026-06-06T00:00:00Z'));

  await page.goto('/products', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  // Kill CSS transitions globally as a belt-and-braces measure.
  await page.addStyleTag({ content: `*,*::before,*::after{
    transition:none!important; animation:none!important; }` });

  await expect(page.getByTestId('product-grid')).toHaveScreenshot('product-grid.png');
});
```

### Tune tolerance to absorb AA noise (not regressions)
Start strict, loosen only enough to clear cross-platform noise.

```typescript
// Per-assertion override when a known-noisy widget needs slack.
await expect(page.getByTestId('chart')).toHaveScreenshot('chart.png', {
  // ~0.5% of pixels may differ (gradient AA), but a real layout shift
  // moves far more than that and still fails.
  maxDiffPixelRatio: 0.005,
  threshold: 0.2,   // per-pixel color distance; default — rarely needs changing
});
```

### Inspect a failure's diff artifacts
On mismatch, Playwright writes three PNGs — compare them.

```bash
# After a failing visual test:
ls test-results/<test-name>/
#   order-summary-actual.png    <- what rendered now
#   order-summary-expected.png  <- the staged baseline
#   order-summary-diff.png      <- highlighted differing pixels
npx playwright show-report      # HTML report with side-by-side + slider
```

---

## Gotchas
1. **Blindly running `--update-snapshots` to fix red** — this bakes whatever rendered (including the regression) into the baseline, permanently hiding the bug. Update only when the change is intended and reviewed.
2. **Cross-platform font/AA differences** — a baseline captured on macOS will diff against a Linux CI render even with identical CSS. Always generate baselines on the same OS that runs the diff (TFactory keys baselines per-target/platform).
3. **`deviceScaleFactor` mismatch** — a retina (2x) capture compared against a 1x baseline differs on every pixel. Pin `deviceScaleFactor` in the project config.
4. **The blinking caret flakes one test in N** — an input with focus shows a caret that's present in some captures and not others. `caret: 'hide'` is required; missing it produces intermittent 1–2px diffs.
5. **Animations caught mid-flight** — spinners, fade-ins and CSS transitions render differently per run. `animations: 'disabled'` freezes them to the end state; also consider injecting `transition:none` CSS for stubborn third-party widgets.
6. **Web-font FOUT changes the shot** — capturing before fonts load grabs a fallback typeface, then a "regression" appears when the real font lands. `await page.evaluate(() => document.fonts.ready)` before asserting.
7. **Full-page screenshots are flake magnets** — any unrelated dynamic region (footer year, live badge) fails the whole page. Prefer a tightly scoped `locator.toHaveScreenshot()` and mask the rest.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| `--update-snapshots` to clear a failing test | Bakes the regression into the baseline; the bug is now "correct" | Update only for intended UI changes, after reviewing the new PNG |
| Generating baselines locally (macOS) for Linux CI | Font/AA rendering differs → every diff is noise, real ones drown | Capture baselines on the runner OS; key them per-target like `visual_baseline` |
| Omitting `animations:'disabled'` / `caret:'hide'` | Moving pixels cause intermittent failures unrelated to real changes | Set both globally in config and per critical assertion |
| Full-page screenshots of dynamic pages | One unrelated dynamic region fails the entire page | Scope to the component locator; mask remaining dynamic regions |
| Cranking `maxDiffPixelRatio` high to stop flake | A large tolerance hides genuine layout regressions too | Fix the flake source (mask/stabilize); keep tolerance tight (~0.005–0.01) |
| Not pinning viewport / `deviceScaleFactor` | Baselines aren't comparable across configs; spurious full-image diffs | Pin viewport, DPR, and color scheme in the Playwright project |
| Snapshotting before fonts/network settle | Captures fallback fonts or half-loaded content; flaky baseline | Wait for `document.fonts.ready` and `networkidle` first |
| Committing `*-actual.png` / `*-diff.png` artifacts as baselines | Diff/actual outputs are run artifacts, not references | Only promote reviewed `expected`/baseline PNGs into the store |
