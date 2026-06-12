---
layout: default
title: Design System
permalink: /design-system/
nav_order: 3
---

# The Factory Design Language

> Date: 2026-06-12
> Status: Canonical — the shared visual language for **all** Factory services
> Scope: PFactory · AIFactory · TFactory · CFactory (and any future `*Factory`)

This document is the single source of truth for how every Factory portal looks
and feels. The goal is simple: **the four services are visibly one family, and
each is instantly distinguishable by a single accent colour.** Same shell, same
type, same tokens, same components — one colour per service.

If you are theming a Factory portal, conform to this document. If this document
and a portal's CSS disagree, the portal is wrong.

---

## 1. The family principle

One base palette, one accent per service.

- **Base:** [Gruvbox](https://github.com/morhetz/gruvbox) — warm, retro-groove,
  terminal heritage. Light **and** dark. Every portal ships both.
- **Accent:** each service owns exactly one Gruvbox hue. The accent is the only
  thing that should differ between two portals sitting side by side.

The cockpit (**CFactory**) is the canonical source of the accent legend, because
it renders all four services together and must keep them distinct:

| Service | Role | Accent hue | Dark hex | Light hex |
|---|---|---|---|---|
| **PFactory** | Plan | Gruvbox **purple** | `#d3869b` | `#8f3f71` |
| **AIFactory** | Code | Gruvbox **yellow** | `#fabd2f` | `#b57614` |
| **TFactory** | Test | Gruvbox **green** | `#b8bb26` | `#79740e` |
| **CFactory** | Cockpit | Gruvbox **blue/aqua** | `#83a598` | `#458588` |

The accent maps to the **`--primary`** and **`--ring`** tokens (see §3). Nothing
else about the palette changes between services.

---

## 2. Typography

Three faces, shared by every portal. Loaded once from Google Fonts in
`index.html`; referenced everywhere through tokens — never hard-code a family.

| Token | Face | Used for |
|---|---|---|
| `--font-sans` | **Hanken Grotesk** | All UI prose, labels, body, buttons |
| `--font-display` | **Archivo** (600–800, wide) | Big page headings, hero/stat numerals |
| `--font-mono` | **JetBrains Mono** | The *data plane* — test ids, verdicts, lane codes, SHAs, reports, terminals, stat values |

The identity is **mono-led**: the Factory products are about code, so anything
that *is* data renders in JetBrains Mono. Sans (Hanken Grotesk) carries the
chrome around it. Archivo is reserved for display-scale headings and the big
numbers on stat cards.

```html
<!-- index.html — the one place fonts are loaded -->
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet"
  href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=Hanken+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" />
```

```css
/* index.css — exposed as tokens */
--font-sans:    'Hanken Grotesk', ui-sans-serif, system-ui, -apple-system, sans-serif;
--font-display: 'Archivo', 'Hanken Grotesk', ui-sans-serif, system-ui, sans-serif;
--font-mono:    'JetBrains Mono', ui-monospace, 'SFMono-Regular', 'Menlo', monospace;
```

> Do **not** use Inter, IBM Plex Sans, or the system stack as the UI sans. Those
> are drift. Hanken Grotesk is the canon.

---

## 3. The colour token contract

All portals use a **Tailwind v4 `@theme` + HSL CSS-variable** system (shadcn
convention). Tokens are defined as bare HSL triples on `:root` (light) and
`.dark` (dark), and surfaced to Tailwind as `--color-*`. This keeps
`bg-background`, `text-foreground`, `border-border`, `bg-primary`, etc. working
identically in every portal.

### Light — `:root`

```css
--background: 48 87% 88%;   /* bg0   #fbf1c7 */
--foreground: 20 5% 22%;    /* fg1   #3c3836 */
--card: 46 80% 92%;         /* cream */
--popover: 46 80% 92%;
--primary: <SERVICE ACCENT — light>;
--secondary: 43 36% 81%;    /* bg1   #ebdbb2 */
--muted: 43 36% 81%;
--muted-foreground: 26 10% 44%;
--accent: 18 96% 35%;       /* orange #af3a03 — secondary highlight */
--destructive: 2 99% 31%;   /* red    #9d0006 */
--success: 146 30% 37%;     /* aqua   #427b58 */
--warning: 36 80% 39%;      /* yellow #b57614 */
--info: 189 90% 25%;        /* blue   #076678 */
--border: 40 33% 73%;       /* bg2   #d5c4a1 */
--input: 40 33% 73%;
--ring: <SERVICE ACCENT — light>;
--radius: 0.5rem;
```

### Dark — `.dark`

```css
--background: 0 0% 16%;     /* bg0  #282828 */
--foreground: 43 36% 81%;   /* fg1  #ebdbb2 */
--card: 20 5% 22%;          /* bg1  #3c3836 */
--popover: 24 7% 29%;       /* bg2  #504945 */
--primary: <SERVICE ACCENT — dark>;
--secondary: 24 7% 29%;     /* bg2 */
--muted: 24 7% 29%;
--muted-foreground: 40 14% 59%;  /* fg4  #a89984 */
--accent: 27 99% 55%;       /* orange #fe8019 — secondary highlight */
--destructive: 6 96% 59%;   /* red    #fb4934 */
--success: 110 33% 62%;     /* aqua   #8ec07c */
--warning: 42 95% 58%;      /* yellow #fabd2f */
--info: 155 16% 58%;        /* blue   #83a598 */
--border: 24 7% 29%;        /* bg2 */
--input: 24 7% 29%;
--ring: <SERVICE ACCENT — dark>;
```

Only `--primary` and `--ring` carry `<SERVICE ACCENT>`. For TFactory:

```css
:root { --primary: 56 80% 26%; --ring: 56 80% 26%; }  /* green #79740e */
.dark { --primary: 61 66% 44%; --ring: 61 66% 44%; }  /* green #b8bb26 */
```

### Semantic colours (identical everywhere)

`success` = Gruvbox aqua, `warning` = Gruvbox yellow, `info` = Gruvbox blue,
`destructive` = Gruvbox red. These are status semantics and must **not** be
re-keyed per service — only the brand accent (`--primary`/`--ring`) changes.

> A service's accent and its semantic colours can collide (e.g. AIFactory's
> yellow accent vs the yellow `warning`, TFactory's green vs the aqua `success`).
> That is acceptable — accent lives on primary actions/active states; semantics
> live on badges/verdicts. Keep them in their own lanes.

---

## 4. Component conventions

| Element | Rule |
|---|---|
| Radius | `--radius: 0.5rem` base; cards/panels `0.5–0.75rem`. Pills/badges fully rounded. |
| Borders | `1px solid hsl(var(--border))` (Gruvbox `bg2`). Borders, not shadows, define structure. |
| Cards / panels | `bg-card` on `bg-background`, 1px border. No heavy elevation; Gruvbox is flat-ish. |
| Data values | Anything that *is* data → `font-mono`. Test ids, verdicts, lane codes, counts, SHAs, durations. |
| Section labels | Small **uppercase mono**, `muted-foreground`, letter-spaced — the CFactory `.mc-stat-l` pattern. |
| Stat numerals | `font-display` or `font-mono`, large, `foreground`. |
| Active / selected | `--primary` (the service accent) on border + a `primary/5–10%` tint fill. |
| Focus | `ring-2 ring-ring ring-offset-2` — keyboard focus is always visible (a11y). |
| Motion | Respect `prefers-reduced-motion`; keep transitions ≤200ms. |
| Scrollbars | Themed: `--border` thumb on `--background` track. |

---

## 5. Conformance checklist

A portal is "Factory-aligned" when:

- [ ] Loads Hanken Grotesk + Archivo + JetBrains Mono (and nothing else) from `index.html`.
- [ ] `--font-sans` = Hanken Grotesk, `--font-display` = Archivo, `--font-mono` = JetBrains Mono.
- [ ] Uses the `@theme` + HSL `--background`/`--foreground`/… token contract (§3).
- [ ] Ships Gruvbox light **and** dark.
- [ ] `--primary` **and** `--ring` = the service's accent from the §1 legend (and nothing else carries the brand hue).
- [ ] Semantic colours (`success`/`warning`/`info`/`destructive`) match §3 exactly — not re-keyed per service.
- [ ] No foreign palettes (no neutral-shadcn, no Inter, no off-brand alt-themes).
- [ ] Components follow §4 (mono data plane, uppercase-mono labels, 1px borders, visible focus).

---

## 6. Current drift (migration status)

As of 2026-06-12, the family already shares Gruvbox but has drifted. This is the
backlog to bring every portal to canon. **TFactory is now the reference
implementation.**

| # | Drift | Affected | Status |
|---|---|---|---|
| A | UI sans split — IBM Plex Sans vs Hanken Grotesk | TFactory, PFactory (Plex) | **TFactory fixed** → Hanken. PFactory pending. |
| B | Token architecture — CFactory uses hand-rolled hex vars, dark-only, not the `@theme`/HSL contract | CFactory | Pending — migrate to §3 contract + ship a light mode. |
| C | Off-brand inherited "Mira (shadcn)" alt-theme (neutral + yellow) | TFactory | **Fixed** — removed. |
| D | No canonical design-language doc | all | **Fixed** — this document. |
| E | Display font (Archivo) only adopted by CFactory | PFactory, AIFactory, TFactory | **TFactory fixed** (`--font-display`). Others pending. |
| F | PFactory's palette is byte-identical to TFactory's → renders **green**, not its legend **purple** | PFactory | Pending — set `--primary`/`--ring` to purple (§1). |
| G | AIFactory light `--primary` is orange `#d65d0e`, not the legend yellow | AIFactory | Pending — align light primary to yellow. |

### What each sibling needs (one small PR each)

- **PFactory** — switch UI sans to Hanken Grotesk; add `--font-display: Archivo`;
  set `--primary`/`--ring` to Gruvbox **purple** (`#8f3f71` / `#d3869b`); drop any
  inherited shadcn alt-theme.
- **AIFactory** — add `--font-display: Archivo`; set light `--primary` to the
  legend **yellow** (`#b57614` / `#fabd2f`) so light and dark agree; keep Hanken.
- **CFactory** — migrate from the hand-rolled hex `:root` to the `@theme` + HSL
  token contract (§3); add a Gruvbox **light** mode; keep the **blue/aqua** accent
  and the Archivo display face it already pioneered.

---

## 7. Reference implementation

TFactory's portal is the conformant reference:

- `apps/frontend-web/index.html` — the canonical font `<link>`.
- `apps/frontend-web/src/index.css` — the `@theme` block, the `:root`/`.dark`
  Gruvbox tokens, and the green accent on `--primary`/`--ring`.

Copy those two files' relevant blocks when conforming a sibling; change only the
two accent lines to the sibling's legend hue.
