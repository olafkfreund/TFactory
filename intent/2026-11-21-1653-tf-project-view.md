---
status: draft
issue: 1653
author: olafkfreund
---

# Intent: URL-scoped projects in the TFactory frontend

## Problem

The active project in TFactory lives in zustand (`selectedProjectId`, `activeProjectId`), `localStorage` (`lastSelectedProjectId`), and server `tab-state.json` — not in the URL. Every project surface (`tfactory` pipeline/specs/verify runs, terminals, editor, context, GitHub issues/PRs, changelog, insights, worktrees, agent tools, skills, cloud, visual reports) renders at `/` regardless of project, and `TFactoryPortal` currently bypasses project selection altogether (`App.tsx:375-382`). Only `/console/:projectId/:specId` carries a project identifier in the path. Links are not shareable, browser back/forward and reload do not preserve project or view, two browser tabs cannot hold two projects reliably, and the header `ProjectSelector` unregisters a project with a single trash-icon click and no confirmation (epic Factory#3857, phase 4; handover Factory#3864).

## Proposed outcome

- Every project view (including TFactory's specs, verify runs, cloud, and visual reports) lives under `/p/:slug[/:view]`, with `/p/:slug` defaulting to the `tfactory` view. The URL is authoritative: project views mount only once the slug resolves and the store syncs. Reload, back/forward, and two tabs on two projects all work without cross-project task bleed. Saved tab state restores open tabs only and never overrides the URL.
- `/` is "My projects" (`WelcomeScreen` project list/entry, no auto-redirect). `/projects` is the Projects admin page holding add (`AddProjectModal`) and delete (typed-name confirmation dialog; keeps today's unregister-only semantics).
- Legacy routes redirect: `/console/:projectId/:specId` redirects to `/p/:slug/console/:specId`, and any unauthenticated deep link returns to its target URL after `/login`.
- The top bar replaces `ProjectSelector` and the `+` button with the shared `ProjectBreadcrumb` (searchable switcher + "All projects…", no delete).
- `PortalSwitcher` keeps the current project slug (`project={{ slug: currentSlug, portals: ['plan', 'build', 'test'] }}`) when hopping to sibling portals, and `CommandPalette` (Cmd-K) offers "Switch project…" (both via re-vendoring the Factory hub `@factory/ui` components at `5f3d231093d30346a584303011412156e8952aad` plus per-project `Switch project: <name>` commands).
- Slug resolution uses `project.slug` when present, else deterministic `slugify(name)` (falling back to `id`), reserving backend slugs first and assigning derived slugs oldest-first so newcomers cannot steal an existing `/p/<slug>`. Canonical slug, raw `id`, or collision-suffixed alias all resolve in the URL and rewrite (`replace`) to the canonical slug.

## Affected users and systems

TFactory web users; `apps/frontend-web` and `.github/workflows/factory-ui-drift.yml` only. Shared `@factory/ui` files vendored byte-identical from `olafkfreund/Factory` (`origin/main`).

## Constraints

- Frontend only; no backend changes (P3 enforcement and phase 3 shared registry cutover run in parallel).
- Vendored `factory-ui` files (`ProjectBreadcrumb.tsx`, `PortalSwitcher.tsx`, `CommandPalette.tsx`) must remain byte-identical to `Factory/shared/factory-ui/` at `HUB_PIN_SHA` so `check_factory_ui_drift.py` passes.
- All user-facing strings in new/modified views use `react-i18next` locale keys (`en`, `fr`, `pt-BR`).
- Existing vitest suites, `tsc --noEmit`, and `eslint . --max-warnings 2817` must stay green.

## Open questions

None. Owner pre-approved the AIFactory reference architecture (`AIFactory#2159`, `Factory#3857` phase 4) for TFactory.
