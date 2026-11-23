---
status: approved
issue: 1665
intent: intent/2026-11-23-1665-adopt-design-tokens.md
---

# Spec: TFactory adopts the shared design tokens

## Design

- **Vendored files.** Vendor the hub's `shared/factory-ui/tokens.css` and
  `tailwind-theme.css` byte-exact into `apps/frontend-web/src/styles/`.
  Re-vendor the hub's updated `PortalSwitcher.tsx` and `CommandPalette.tsx`
  into `src/components/`.
- **Drift pin.** Bump `HUB_PIN_SHA` in `.github/workflows/factory-ui-drift.yml`
  to Factory main `7f3ca3fdd4b8dec0386732d358ad88a166daecc5` (the #3875 merge).
- **`index.css`:**
  - import `./styles/tokens.css` and `./styles/tailwind-theme.css`;
  - delete the local colour `@theme` entries, `:root`, `.dark` and both
    `[data-theme="gruvbox"]` blocks;
  - keep the non-colour rules (animations, prose, scrollbar, and so on), but
    point them at the tokens.
- **`index.html`:**
  - `<html data-portal="test">`;
  - the pre-paint script only toggles `.dark` (from `tfactory-theme`:
    light, dark or system) and no longer sets `data-theme`;
  - `theme-color` uses the new dark canvas `#201f1d`.
- **Code.** Remove the colour-theme setter (the `tfactory-color-theme` store
  value) only if nothing else needs it; otherwise leave it as a no-op.
- **Call sites.** About 110 `hsl(var(--x))` and `var(--x)` uses in TSX, TS
  and CSS move to Tailwind utilities (`bg-popover`, `text-success` and so
  on) or `var(--color-x)`:
  - `hsl(var(--x) / a)` becomes `color-mix(in oklch, var(--color-x) a%, transparent)`,
    or the utility's `/a` modifier;
  - xterm and terminal colours read `getComputedStyle` on `--color-*`.
- **README C2 notes.** Fix the call sites the notes list: `dropdown-menu.tsx`
  and `button.tsx`.

## Alternatives rejected

- Keeping the old variables as aliases forever: two sources of truth.
- Restyling the layout now: that is C2 step 6.

## Risks

- **Missed call sites** render transparent. Mitigation: grep returns zero
  `hsl(var(--`, and the owner side-by-side check covers both modes.
- **Third-party colour configuration** (xterm, charts) may need the computed
  values.

## Verification

- `npm -w apps/frontend-web run typecheck`, `test` and `lint` (no new
  errors).
- The vite build succeeds.
- The hub drift gate passes locally at the new pin.
- `grep -rn "hsl(var(--" apps/frontend-web/src` returns 0.
- A browser check of dark and light on the live release.
