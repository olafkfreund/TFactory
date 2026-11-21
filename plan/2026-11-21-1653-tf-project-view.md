---
status: draft
issue: 1653
spec: spec/2026-11-21-1653-tf-project-view.md
coders:
  1: codex   # pure slug-resolution, collision-alias precedence, and route-parsing logic + unit tests
  2: agy     # mechanical byte-exact vendor of @factory/ui files, drift workflow update, and breadcrumb test
  3: codex   # self-contained ProjectsPage with typed-name unregister confirmation + i18n keys + test
  4: codex   # authoritative URL-sync effect, stale-task request counter, login-return and console slug resolution
  5: agy     # mechanical route moves in App.tsx, ProjectTabBar/AppSettings wiring, ProjectSelector deletion, routing tests
reviewers:
  - codex
  - agy
---

# Plan: URL-scoped projects in the TFactory frontend

Self-contained summary of approved decisions:
- Every project view (`tfactory`, `terminals`, `editor`, `context`, `github-issues`, `github-prs`, `changelog`, `insights`, `worktrees`, `agent-tools`, `skills`, `cloud`, `visual-reports`) lives under `/p/:slug[/:view]` (default `'tfactory'`).
- Pure `lib/project-route.ts` handles `slugify`, oldest-first `projectSlugs` (reserving backend `project.slug` first), `findProjectByRef` (canonical > id > alias precedence), `parseRoute`, and `projectPath`.
- `AuthenticatedApp` syncs the project store to the URL in one step with `clearTasks()`, gates all project views (including `TFactoryPortal`) until `activeProjectId === urlProject.id && selectedProjectId === urlProject.id`, keeps `TerminalGrid` mounted across project switches under `/p/:slug`, and rewrites id/alias URLs to canonical `/p/:slug`.
- `/` renders `WelcomeScreen` ("My projects"); `/projects` renders `ProjectsPage` (add via `AddProjectModal`, delete via typed-name confirm calling `removeProject`); `/p/:slug/console/:specId` renders `ConsolePage` with legacy `/console/:projectId/:specId` redirecting via `useEnsureProjects()`; logged-out deep links round-trip through `sessionStorage['tf-return-to']`.
- Top bar uses vendored `ProjectBreadcrumb` and passes `project={{ slug, portals: ['plan', 'build', 'test'] }}` to `PortalSwitcher`; `CommandPalette` gets `projects`/`onSwitchProject` plus per-project switch commands; `AppSettings` shows a read-only project label; `ProjectSelector.tsx` is deleted.
- `@factory/ui` components (`ProjectBreadcrumb.tsx`, `PortalSwitcher.tsx`, `CommandPalette.tsx`) are vendored byte-identical from Factory `5f3d231093d30346a584303011412156e8952aad` with `HUB_PIN_SHA` updated in `.github/workflows/factory-ui-drift.yml`.

All frontend paths below are relative to `apps/frontend-web/`.

## Steps

1. **(codex — slug-resolution and URL-precedence logic)** `src/shared/types/project.ts:5-15` and `src/lib/project-route.ts` (new, 1-85) + `src/lib/__tests__/project-route.test.ts` (new, 1-80):
   Add `slug?: string;` to `Project` in `src/shared/types/project.ts:8`. Create `src/lib/project-route.ts` exporting `PROJECT_VIEWS` (all 13 `SidebarView` values from `src/components/Sidebar.tsx:52`, default `'tfactory'`), `ProjectView`, `AppRoute`, `slugify`, `projectSlugs`, `projectSlug`, `findProjectByRef`, `parseRoute`, and `projectPath`. Create unit tests in `src/lib/__tests__/project-route.test.ts` covering NFKD slugify fallback, oldest-first collision suffixes, backend slug reservation, canonical > id > alias resolution (including after older twin removal), `parseRoute` default to `'tfactory'`, and `projectPath` round-trip.
   → verify by `cd apps/frontend-web && npx vitest run src/lib/__tests__/project-route.test.ts && npm run typecheck`
   Traps: TFactory's default view is `'tfactory'` (not AIFactory's `'kanban'`), and `PROJECT_VIEWS` must include `'cloud'` and `'visual-reports'`.

2. **(agy — mechanical vendor & drift workflow update)** `src/components/ProjectBreadcrumb.tsx` (new, 1-152), `src/components/PortalSwitcher.tsx:1-84`, `src/components/CommandPalette.tsx:1-244`, `.github/workflows/factory-ui-drift.yml:5-39`, and `src/components/__tests__/ProjectBreadcrumb.test.tsx` (new, 1-56):
   Copy `ProjectBreadcrumb.tsx`, `PortalSwitcher.tsx`, and `CommandPalette.tsx` byte-for-byte from `/mnt/code/Source-home/GitHub/Factory` at `5f3d231093d30346a584303011412156e8952aad` (`git -C /mnt/code/Source-home/GitHub/Factory show 5f3d231093d30346a584303011412156e8952aad:shared/factory-ui/<file>`). In `.github/workflows/factory-ui-drift.yml:6-25,38`, add `apps/frontend-web/src/components/ProjectBreadcrumb.tsx` to the header comment and `pull_request.paths`, and bump `HUB_PIN_SHA` to `"5f3d231093d30346a584303011412156e8952aad"`. Add `src/components/__tests__/ProjectBreadcrumb.test.tsx`.
   → verify by `python3 /mnt/code/Source-home/GitHub/Factory/scripts/check_factory_ui_drift.py --canonical /mnt/code/Source-home/GitHub/Factory/shared/factory-ui --service tfactory --root . && cd apps/frontend-web && npx vitest run src/components/__tests__/ProjectBreadcrumb.test.tsx`
   Traps: never format or edit vendored files locally — any 1-byte difference fails `check_factory_ui_drift.py`.

3. **(codex — ProjectsPage typed-name confirmation & i18n)** `src/pages/ProjectsPage.tsx` (new, 1-101), `src/pages/__tests__/ProjectsPage.test.tsx` (new, 1-45), and `src/shared/i18n/locales/{en,fr,pt-BR}/welcome.json:1-16`:
   Add `projectsPage` keys (`title`, `add`, `empty`, `open`, `delete`, `unregisterTitle`, `unregisterBody`, `nameLabel`, `cancel`, `confirm`, `notFound`, `backHome`, with "TFactory" in `unregisterBody`) to `en/welcome.json`, `fr/welcome.json`, and `pt-BR/welcome.json`. Create `src/pages/ProjectsPage.tsx` rendering the project list (`name`, `path`, `/p/<slug>` badge, `Open` button, `Delete` button opening a `Dialog` whose confirm button is disabled until `typedName === deleting.name` and calls `removeProject(deleting.id)`). Add `src/pages/__tests__/ProjectsPage.test.tsx`.
   → verify by `cd apps/frontend-web && npx vitest run src/pages/__tests__/ProjectsPage.test.tsx`
   Traps: all user-facing strings must use `useTranslation('welcome')` keys (AGENTS.md i18n rule); delete only unregisters.

4. **(codex — URL-precedence state sync, stale-task guard, console & auth hooks)** `src/stores/task-store.ts:475-497`, `src/hooks/useEnsureProjects.ts` (new, 1-20), `src/pages/ConsolePage.tsx:1-70`, and `src/App.tsx:44-245,530-557`:
   - In `src/stores/task-store.ts:475-497`, add module-level `let latestTasksRequest = 0;` in `loadTasks(projectId)` so responses where `request !== latestTasksRequest` are discarded before `store.setTasks`.
   - Create `src/hooks/useEnsureProjects.ts` calling `loadProjects()` once when `projects.length === 0`.
   - Update `src/pages/ConsolePage.tsx:23-69` to read `{ slug, specId } = useParams<{ slug: string; specId: string }>()`, call `useEnsureProjects()`, resolve `project = findProjectByRef(projects, slug)`, compute `projectId = project?.id ?? slug` and `slugOrRef = project ? projectSlug(projects, project) : slug`, and point the header `<Link>` to `project ? `/p/${slugOrRef}` : '/'`.
   - In `src/App.tsx:44-245,530-557`, add `RETURN_TO_KEY = 'tf-return-to'`, `RedirectToLogin`, `LegacyConsoleRedirect`, `authChecked` gate in `App()` (`checkAuth().finally(() => setAuthChecked(true))`), and in `AuthenticatedApp`: `parseRoute(pathname)`, `projectsLoaded` state (`loadProjects().finally(() => setProjectsLoaded(true))`), `slugs = projectSlugs(projects)`, `urlProject`, `urlSlug`, `inSync`, `selectedProject = inSync ? urlProject : undefined`, the URL->store sync effect (calling `clearTasks()`, `openProjectTab(urlProjectId)`, `selectProject(urlProjectId)`), the canonical slug `replace` effect, and the `tf-return-to` login-return effect at `/`.
   → verify by `cd apps/frontend-web && npm run typecheck`
   Traps: do not edit `loadProjects()` in `project-store.ts`; keep `useSessionKeepAlive({ onAuthLost: logout })` and `ViewStateProvider` in `App.tsx`.

5. **(agy — mechanical route moves, header/settings wiring, routing test suite)** `src/App.tsx:124-150,317-557`, `src/components/ProjectTabBar.tsx:1-235`, `src/components/settings/AppSettings.tsx:49,118-123,169-171,306-313`, `src/components/settings/ProjectSelector.tsx` (delete), and `src/__tests__/project-routing.test.tsx` (new, 1-200):
   - In `src/App.tsx:124-150`, derive `activeView` from `route.kind === 'project' ? route.view : 'tfactory'`, wire `goProject` and `setActiveView`, add `Switch project: <name>` commands to `paletteCommands`, and pass `projects={breadcrumbProjects}` + `onSwitchProject={(slug) => void navigate(projectPath(slug))}` to `<CommandPalette>`.
   - In `src/App.tsx:359-443`, pass `currentSlug={urlSlug}` and `onViewAllProjects={() => void navigate('/projects')}` to `<ProjectTabBar>`, navigate on tab select/close, mount `<TerminalGrid projectPath={urlProject.path} ... />` when `route.kind === 'project' && urlProject`, render not-found when `route.kind === 'project' && !urlProject && projectsLoaded`, gate all project views (`tfactory` -> `<TFactoryPortal />`, `editor`, `worktrees`, `context`, `github-issues`, `github-prs`, `changelog`, `insights`, `agent-tools`, `skills`, `cloud`, `visual-reports`) under `selectedProject`, render `<ProjectsPage>` when `route.kind === 'projects'`, and `<WelcomeScreen>` when `route.kind === 'home'`.
   - In `src/App.tsx:539-556`, add routes `/p/:slug/console/:specId` (`<ConsolePage />`), `/console/:projectId/:specId` (`<LegacyConsoleRedirect />`), and use `<RedirectToLogin />` when unauthenticated.
   - In `src/components/ProjectTabBar.tsx:1-235`, replace `ProjectSelector` and the `+` button with `<ProjectBreadcrumb>`, pass `project={currentSlug ? { slug: currentSlug, portals: ['plan', 'build', 'test'] } : undefined}` to `<PortalSwitcher current="test" needsCount={needsCount} />`, and move `overflow-x-auto` to the inner tab strip `div`.
   - In `src/components/settings/AppSettings.tsx:49,118-123,169-171,306-313`, remove `ProjectSelector` import and `handleProjectChange`, use `activeProjectId || selectedProjectId` to find `selectedProject`, and render a read-only bordered card showing `selectedProject.name` and `selectedProject.path`. Delete `src/components/settings/ProjectSelector.tsx`.
   - Add `src/__tests__/project-routing.test.tsx` testing: URL beats saved tab state, `:id` rewrites to canonical slug, unknown slug shows not-found without mounting views, back/forward switches projects without task bleed, `TerminalGrid` mounts once across a project switch, `/p/alpha/insights` selects view, `/` and `/projects` render their pages, logged-out deep link returns after login, legacy `/console/:projectId/:specId` redirects to `/p/:slug/console/:specId`, and stale `loadTasks` response is dropped.
   → verify by `cd apps/frontend-web && npx vitest run && npm run typecheck && npm run lint`
   Traps: `eslint . --max-warnings 2817` ratchet in `package.json:10` fails on net-new warnings (`void navigate(...)` on promises, no unused imports). Keep TFactory's `CloudCheckDialog` (`App.tsx:504`) and `ChatLauncher` (`App.tsx:448`).

## Tests

```bash
cd apps/frontend-web && npx vitest run && npm run typecheck && npm run lint
python3 /mnt/code/Source-home/GitHub/Factory/scripts/check_factory_ui_drift.py \
  --canonical /mnt/code/Source-home/GitHub/Factory/shared/factory-ui \
  --service tfactory --root .
```

Expected: all vitest suites pass (including the 4 new test files), `tsc --noEmit` exits 0, `eslint` exits 0 within warning budget, and `check_factory_ui_drift.py` reports byte-exact match for `CommandPalette.tsx`, `PortalSwitcher.tsx`, and `ProjectBreadcrumb.tsx`.

## Rollback

Revert the PR commit. Backend routes, `projects.json`, and persisted tab state are untouched, restoring `ProjectSelector` and state-only project selection.
