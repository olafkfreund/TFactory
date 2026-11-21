---
status: approved
issue: 1653
intent: intent/2026-11-21-1653-tf-project-view.md
---

# Spec: URL-scoped projects in the TFactory frontend

## Design

All code lives under `apps/frontend-web/src` (plus `.github/workflows/factory-ui-drift.yml`).

1. **`lib/project-route.ts` (new, pure) & `shared/types/project.ts`.**
   - Add optional `slug?: string` to `Project` in `shared/types/project.ts`.
   - `PROJECT_VIEWS` mirrors `SidebarView` in `components/Sidebar.tsx:52`: `['tfactory', 'terminals', 'editor', 'context', 'github-issues', 'github-prs', 'changelog', 'insights', 'worktrees', 'agent-tools', 'skills', 'cloud', 'visual-reports'] as const`. Default view is `'tfactory'`.
   - `slugify(name, fallback)`: NFKD, lowercase, non-`[a-z0-9]+` to `-`, trim leading/trailing `-`, fallback to `id` when empty.
   - `projectSlugs(projects)`: `Map<id, slug>`. Reserves `p.slug` first; sorts remaining projects by `createdAt` ascending (tie-break `id.localeCompare`) and assigns `base`, else `${base}-${id.slice(0, 8)}`, else `${withId}-${n}` (`n >= 2`).
   - `findProjectByRef(projects, ref)`: case-insensitive lookup with precedence: canonical slug (`projectSlugs`) > `p.id` > aliases (`p.slug`, plain `slugify(p.name, p.id)`, or `${slugify(p.name, p.id)}-${p.id.slice(0, 8)}`).
   - `parseRoute(pathname)`: `/p/<ref>[/<view>]` (URI-decoded `ref`, unknown/omitted view -> `'tfactory'`) -> `{ kind: 'project', ref, view }`; `/projects/?` -> `{ kind: 'projects' }`; otherwise `{ kind: 'home' }`.
   - `projectPath(slug, view?)`: `/p/${encodeURIComponent(slug)}${view && view !== 'tfactory' ? `/${view}` : ''}`.

2. **Route mapping table.**

| Old route / state | New route | Redirect / resolution behaviour |
| --- | --- | --- |
| `/` with no project or landing | `/` | Renders "My projects" (`WelcomeScreen`); no auto-redirect to last project |
| Project selector add / trash-delete | `/projects` | Renders `ProjectsPage` (list, Open -> `/p/:slug`, Add -> `AddProjectModal`, Delete -> typed-name confirm calling `removeProject(id)`) |
| `/` (`activeView === 'tfactory'`, previously global) | `/p/:slug` (or `/p/:slug/tfactory`) | Default project view; mounts `TFactoryPortal` (specs, verify runs, verdicts, reports) gated on active project == URL project |
| `/` (`activeView === '<view>'` in zustand) | `/p/:slug/:view` (`terminals`, `editor`, `context`, `github-issues`, `github-prs`, `changelog`, `insights`, `worktrees`, `agent-tools`, `skills`, `cloud`, `visual-reports`) | `activeView` derived from URL; `setActiveView(v)` navigates to `/p/:slug/:view` (`tfactory` omits suffix) |
| `/p/:ref[/:view]` where `:ref` is `project.id` or non-canonical alias | `/p/:slug[/:view]` | `findProjectByRef` resolves project; effect rewrites URL via `navigate(projectPath(urlSlug, route.view) + search, { replace: true })` |
| `/p/:ref/:unknownView` | `/p/:slug` | `parseRoute` falls back to default view `'tfactory'` |
| `/p/:unknownRef` (after `projectsLoaded`) | `/p/:unknownRef` | Renders "Project not found: `<ref>`" with link back to `/` (`welcome:projectsPage.notFound`); no project views mounted |
| `/console/:projectId/:specId` | `/p/:slug/console/:specId` | `<LegacyConsoleRedirect />` waits for `useEnsureProjects()`, resolves `:projectId` via `findProjectByRef`, `<Navigate replace>` to `/p/:slug/console/:specId` |
| Unauthenticated deep link (e.g. `/p/alpha/cloud`) | `/login` -> `/p/alpha/cloud` | `<RedirectToLogin />` saves `pathname + search` to `sessionStorage['tf-return-to']` after `authChecked`; on login at `/`, effect consumes key and `navigate(to, { replace: true })` |

3. **Authoritative URL & task/terminal isolation (`App.tsx`, `stores/task-store.ts`, `hooks/useEnsureProjects.ts`, `pages/ConsolePage.tsx`).**
   - `App` waits for initial `checkAuth().finally(() => setAuthChecked(true))` before rendering `<Routes>` so cold deep links are not bounced to `/login` before cookie/token check completes.
   - In `AuthenticatedApp`, `urlProject = route.kind === 'project' ? findProjectByRef(projects, route.ref) : undefined`. When `projectsLoaded && urlProject`, an effect checks if `activeProjectId === urlProject.id && selectedProjectId === urlProject.id`; on mismatch it calls `useTaskStore.getState().clearTasks()`, `openProjectTab(urlProject.id)`, and `selectProject(urlProject.id)` in one synchronous step.
   - `inSync = !!urlProject && activeProjectId === urlProject.id && selectedProjectId === urlProject.id`; `selectedProject = inSync ? urlProject : undefined`. All project views (including `TFactoryPortal`, `cloud`, `visual-reports`) render only when `selectedProject` is defined.
   - `TerminalGrid` mounts when `route.kind === 'project' && urlProject` (receiving `urlProject.path`) outside the `selectedProject` store-sync gate so switching `/p/alpha` -> `/p/beta` keeps xterm/PTY instances alive without remounting; it unmounts on `/` and `/projects`.
   - `stores/task-store.ts`: module-level `latestTasksRequest` counter in `loadTasks(projectId)` drops stale out-of-order responses so project A's late response cannot overwrite project B's tasks.
   - `pages/ConsolePage.tsx`: reads `{ slug, specId } = useParams()`, calls `useEnsureProjects()`, resolves `project = findProjectByRef(projects, slug)`, builds `taskId = `${project?.id ?? slug}:${specId}``, and links "Back to portal" to `project ? `/p/${projectSlug(projects, project)}` : '/'`.

4. **Top bar, PortalSwitcher, CommandPalette, Settings & Drift gate.**
   - Re-vendor `ProjectBreadcrumb.tsx`, `PortalSwitcher.tsx`, and `CommandPalette.tsx` byte-identical from `Factory` `origin/main` (`5f3d231093d30346a584303011412156e8952aad`), and bump `HUB_PIN_SHA` + `paths` in `.github/workflows/factory-ui-drift.yml`.
   - `ProjectTabBar.tsx`: replace `ProjectSelector` and `+` button with `<ProjectBreadcrumb projects={breadcrumbProjects} currentSlug={currentSlug} onSelect={...} onViewAll={onViewAllProjects} />`. Pass `project={currentSlug ? { slug: currentSlug, portals: ['plan', 'build', 'test'] } : undefined}` to `<PortalSwitcher current="test" needsCount={needsCount} ... />`. Move `overflow-x-auto` to the left tab-strip `div` so the breadcrumb popover is never clipped.
   - `CommandPalette` in `App.tsx`: pass `projects={breadcrumbProjects}` and `onSwitchProject={(slug) => navigate(projectPath(slug))}` (enabling the built-in `"Switch project..."` mode) plus per-project `Switch project: <name>` commands in `paletteCommands`.
   - `components/settings/AppSettings.tsx`: replace `ProjectSelector` with a read-only display of `selectedProject.name` and `selectedProject.path`; delete `components/settings/ProjectSelector.tsx`.
   - Add `welcome:projectsPage.*` i18n keys to `en`, `fr`, and `pt-BR` `welcome.json`.

## Alternatives rejected

- Keep `TFactoryPortal` outside project gating at `/`: violates issue #1653 ("TFactory views include specs, verify runs and reports: all go under `/p/:slug`" and "`/` is My projects").
- Keep `ProjectSelector` in `AppSettings`: allows store-only project mutation that immediately fights the authoritative URL effect.
- Separate `<Route path="/p/:slug/:view">` elements: unmounts `TerminalGrid` on view changes; parsing inside one `/*` route preserves terminal state.

## Risks

- Existing bookmarks to `/console/:projectId/:specId` or `/`: mitigated by `LegacyConsoleRedirect` and `WelcomeScreen` at `/`.
- ESLint `--max-warnings 2817` or drift check failure: mitigated by byte-copying from Factory `5f3d231093d30346a584303011412156e8952aad` and running both gates.

## Verification

Unit and integration tests in `src/lib/__tests__/project-route.test.ts`, `src/components/__tests__/ProjectBreadcrumb.test.tsx`, `src/pages/__tests__/ProjectsPage.test.tsx`, and `src/__tests__/project-routing.test.tsx` (reload, back/forward, two projects task isolation, terminal single-mount across project switch, legacy console redirect, login return). Full vitest, `tsc --noEmit`, `eslint`, and `check_factory_ui_drift.py`.
