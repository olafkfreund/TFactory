---
status: approved
issue: 1665
author: olafkfreund
parent: olafkfreund/Factory#3873 (Phase C2 step 1)
---

# Intent: TFactory adopts the shared Factory design tokens

## Problem

TFactory carries its own theme:
- `:root`, `.dark` and `[data-theme=gruvbox]` blocks in `index.css`;
- about 110 direct `hsl(var(--x))` uses;
- muted text below AA on cards;
- a separate `data-theme` switch.

The suite's shared tokens now exist in the hub (Factory#3875).

## Proposed outcome

TFactory renders from the hub tokens in dark and light:
- every text pair is AA;
- the Test accent (`data-portal="test"`) is set;
- the vendored copies are byte-exact under the drift gate.

The owner checks dark and light side by side here before the other portals
adopt.

## Affected users and systems

TFactory web UI (`apps/frontend-web`) and its factory-ui drift workflow.

## Constraints

- Colour and type only, no layout change.
- The vendored hub files are never edited in the portal.
- The theme toggle (Light, Dark, System) keeps working.

## Open questions

None. The decisions are in the approved Factory#3873 plan, C0 and C2.
