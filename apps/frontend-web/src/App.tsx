import { useState, useEffect, useMemo, useCallback } from 'react';
import { Routes, Route, Navigate, Link, useLocation, useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { TooltipProvider } from './components/ui/tooltip';
import { Toaster } from './components/ui/toaster';
import { AppShell, type AppShellLabels } from './components/factory-ui/AppShell';
import type { PipelineStripProps } from './components/factory-ui/PipelineStrip';
import { useTFactoryShellAdapter, type SidebarView } from './components/Sidebar';
import { CommandPalette, type PaletteCommand, type PaletteTask } from './components/CommandPalette';
import { ClaudeCodeStatusBadge } from './components/ClaudeCodeStatusBadge';
import { CLIToolStatusBadge } from './components/CLIToolStatusBadge';
import { OpenAIEndpointsStatusBadge } from './components/OpenAIEndpointsStatusBadge';
import { RateLimitIndicator } from './components/RateLimitIndicator';
import { apiRequest } from './lib/api-client';
import { ProjectsPage } from './pages/ProjectsPage';
import { TerminalGrid } from './components/TerminalGrid';
import { Worktrees } from './components/Worktrees';
import { Context } from './components/context/Context';
import { GitHubIssues } from './components/GitHubIssues';
import { GitHubPRs } from './components/github-prs/GitHubPRs';
import { Changelog } from './components/changelog/Changelog';
import { Insights } from './components/Insights';
import { ChatLauncher } from './components/ChatLauncher';
import { AgentTools } from './components/AgentTools';
import { SkillsPage } from './components/SkillsPage';
import { CloudAssessmentPage } from './components/settings/sections/CloudAssessmentPage';
import { VisualReportsPage } from './components/settings/sections/VisualReportsPage';
import { WelcomeScreen } from './components/WelcomeScreen';
import { AddProjectModal } from './components/AddProjectModal';
import { ProjectSettingsPage, PreferencesPage } from './components/settings';
import { TaskCreationWizard } from './components/TaskCreationWizard';
import { CloudCheckDialog } from './components/CloudCheckDialog';
import { TaskDetailModal } from './components/task-detail';
import { TFactoryPortal } from './components/tfactory/TFactoryPortal';
import { OnboardingWizard } from './components/onboarding';
import { ProjectSwitchLoadingModal } from './components/ProjectSwitchLoadingModal';
import { LoginPage } from './pages/LoginPage';
import { EditorPage } from './pages/EditorPage';
import { ConsolePage } from './pages/ConsolePage';
import { useEnsureProjects } from './hooks/useEnsureProjects';
import {
  findProjectByRef,
  isBareSlugChooser,
  parseRoute,
  projectPath,
  projectSlugs,
  projectsForBareSlug,
} from './lib/project-route';
import { ViewStateProvider } from './contexts/ViewStateContext';
import { PipelineStripContext } from './contexts/PipelineStripContext';
import { useProjectStore, loadProjects } from './stores/project-store';
import { useTaskStore, loadTasks } from './stores/task-store';
import { useSettingsStore, loadSettings, saveSettings } from './stores/settings-store';
import { useAuthStore } from './stores/auth-store';
import { useSessionKeepAlive } from './hooks/useSessionKeepAlive';
import { useIpcListeners } from './hooks/useIpc';
import { UI_SCALE_MIN, UI_SCALE_MAX, UI_SCALE_DEFAULT } from './shared/constants';
import type { Project } from './shared/types';

const RETURN_TO_KEY = 'tf-return-to';
const SIDEBAR_COLLAPSED_KEY = 'tfactory-sidebar-collapsed';
const isMac = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform);

function RedirectToLogin() {
  const { pathname, search } = useLocation();
  try {
    if (pathname !== '/' && pathname !== '/login') sessionStorage.setItem(RETURN_TO_KEY, pathname + search);
  } catch {
    // sessionStorage may be unavailable.
  }
  return <Navigate to="/login" replace />;
}

function LegacyConsoleRedirect() {
  const { projectId, specId } = useParams<{ projectId: string; specId: string }>();
  const { ready, error, retry } = useEnsureProjects();
  const { search } = useLocation();
  const { t } = useTranslation('welcome');
  const projects = useProjectStore((state) => state.projects);
  if (!ready) return null;
  if (error) return <div role="alert">{error} <button onClick={retry}>{t('projectsPage.retry')}</button></div>;
  const project = projects.find((p) => p.id.toLowerCase() === projectId?.toLowerCase());
  if (!project) return <div>{t('projectsPage.notFound', { ref: projectId })}</div>;
  const slug = projectSlugs(projects).get(project.id) ?? project.id;
  return <Navigate to={`${projectPath(slug)}/console/${encodeURIComponent(specId ?? '')}${search}`} replace />;
}

function AuthenticatedApp() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const route = useMemo(() => parseRoute(pathname), [pathname]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [projectLoadAttempt, setProjectLoadAttempt] = useState(0);

  // Stores
  const projects = useProjectStore((state) => state.projects);
  const projectError = useProjectStore((state) => state.error);
  const selectedProjectId = useProjectStore((state) => state.selectedProjectId);
  const activeProjectId = useProjectStore((state) => state.activeProjectId);
  const isSwitchingProject = useProjectStore((state) => state.isSwitchingProject);
  const tasks = useTaskStore((state) => state.tasks);
  const settings = useSettingsStore((state) => state.settings);
  const updateStoreSettings = useSettingsStore((state) => state.updateSettings);
  const logout = useAuthStore((state) => state.logout);

  useIpcListeners();
  useSessionKeepAlive({ onAuthLost: logout });

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [isNewTaskDialogOpen, setIsNewTaskDialogOpen] = useState(false);
  const [isCloudCheckOpen, setIsCloudCheckOpen] = useState(false);
  const [isAddProjectModalOpen, setIsAddProjectModalOpen] = useState(false);
  const [isOnboardingOpen, setIsOnboardingOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [strip, setStrip] = useState<PipelineStripProps | undefined>(undefined);
  const [needsCount, setNeedsCount] = useState(0);
  const [version, setVersion] = useState<string | undefined>(undefined);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
    } catch {
      return false;
    }
  });

  const handleSidebarCollapsedChange = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
    } catch {
      // storage may be unavailable
    }
  }, []);

  const slugs = useMemo(() => projectSlugs(projects), [projects]);
  const switcherProjects = useMemo(
    () => projects.map((p) => ({ slug: slugs.get(p.id) ?? p.id, name: p.name, repoLabel: p.path })),
    [projects, slugs]
  );
  const breadcrumbProjects = useMemo(
    () => projects.map((p) => ({ slug: slugs.get(p.id) ?? p.id, name: p.name })),
    [projects, slugs]
  );
  const urlProject = route.kind === 'project' && !projectError ? findProjectByRef(projects, route.ref) : undefined;
  const urlProjectId = urlProject?.id;
  const selectedTask = useMemo(
    () =>
      selectedTaskId && urlProjectId
        ? tasks.find((task) => task.projectId === urlProjectId && (task.id === selectedTaskId || task.specId === selectedTaskId)) ?? null
        : null,
    [selectedTaskId, tasks, urlProjectId]
  );
  const urlSlug = urlProjectId ? slugs.get(urlProjectId) : undefined;
  const activeView: SidebarView = route.kind === 'project' ? route.view : 'tfactory';
  const navProject = urlProject;

  const goProject = useCallback(
    (id: string, view?: string) => {
      const slug = slugs.get(id) ?? id;
      void navigate(projectPath(slug, view));
    },
    [navigate, slugs]
  );

  const setActiveView = useCallback(
    (view: SidebarView) => {
      if (navProject) goProject(navProject.id, view);
      else if (view === 'tfactory') void navigate('/tests');
    },
    [navProject, goProject, navigate]
  );

  const openProjectSettings = useCallback(() => {
    if (navProject) goProject(navProject.id, 'settings');
    else void navigate('/preferences');
  }, [navProject, goProject, navigate]);

  const openPreferences = useCallback(() => {
    void navigate('/preferences');
  }, [navigate]);

  const inSync = !!urlProjectId && activeProjectId === urlProjectId && selectedProjectId === urlProjectId;

  useEffect(() => {
    if (!projectsLoaded || projectError || !urlProjectId) return;
    const state = useProjectStore.getState();
    if (state.activeProjectId === urlProjectId && state.selectedProjectId === urlProjectId) return;
    useTaskStore.getState().clearTasks();
    state.openProjectTab(urlProjectId);
    if (useProjectStore.getState().selectedProjectId !== urlProjectId) state.selectProject(urlProjectId);
  }, [projectsLoaded, projectError, urlProjectId, activeProjectId, selectedProjectId]);

  useEffect(() => {
    if (route.kind === 'project' && urlSlug && route.ref !== urlSlug) {
      void navigate(projectPath(urlSlug, route.view) + search, { replace: true });
    }
  }, [route, urlSlug, search, navigate]);

  useEffect(() => {
    if (route.kind !== 'home' || pathname !== '/') return;
    try {
      const to = sessionStorage.getItem(RETURN_TO_KEY);
      sessionStorage.removeItem(RETURN_TO_KEY);
      if (to?.startsWith('/') && !to.startsWith('//')) void navigate(to, { replace: true });
    } catch {
      // sessionStorage may be unavailable.
    }
  }, [route.kind, pathname, navigate]);

  // Command palette (⌘K / Ctrl-K) and ⌘1–9 project switching
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target as HTMLElement)?.isContentEditable
      ) {
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key >= '1' && e.key <= '9') {
        const index = parseInt(e.key, 10) - 1;
        const target = projects[index];
        if (target) {
          e.preventDefault();
          goProject(target.id, route.kind === 'project' ? route.view : undefined);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [projects, goProject, route]);

  // Needs-you count polling (#148/#149)
  useEffect(() => {
    let active = true;
    const load = () => {
      apiRequest<{ count: number }>('/needs-you/count')
        .then((res) => {
          if (active && res.success && res.data) setNeedsCount(res.data.count);
        })
        .catch(() => {
          /* best-effort badge */
        });
    };
    load();
    const timer = setInterval(load, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    void window.API?.getAppVersion?.().then((v: unknown) => {
      if (typeof v === 'string' && v) setVersion(v);
    });
  }, []);

  const paletteCommands: PaletteCommand[] = useMemo(() => {
    const views: { id: SidebarView; label: string }[] = [
      { id: 'tfactory', label: 'Tests' },
      { id: 'editor', label: 'Editor' },
      { id: 'insights', label: 'Insights' },
      { id: 'terminals', label: 'Terminals' },
      { id: 'agent-tools', label: 'Agent Tools' },
      { id: 'skills', label: 'Skills' },
      { id: 'changelog', label: 'Changelog' },
      { id: 'worktrees', label: 'Worktrees' },
      { id: 'context', label: 'Context' },
      { id: 'cloud', label: 'Cloud' },
      { id: 'visual-reports', label: 'Visual Reports' },
      { id: 'settings', label: 'Project Settings' },
    ];
    const nav: PaletteCommand[] = views.map((v) => ({
      id: `view-${v.id}`,
      group: 'Go to',
      label: v.label,
      keywords: v.id,
      run: () => {
        setActiveView(v.id);
      },
    }));
    const switchProject: PaletteCommand[] = projects.map((p) => ({
      id: `project-${p.id}`,
      group: 'Go to',
      label: `Switch project: ${p.name}`,
      keywords: `project switch ${slugs.get(p.id) ?? ''}`,
      run: () => {
        goProject(p.id);
      },
    }));
    const actions: PaletteCommand[] = [
      {
        id: 'act-preferences',
        group: 'Actions',
        label: 'Open preferences',
        keywords: 'preferences settings config theme',
        run: openPreferences,
      },
      {
        id: 'act-project-settings',
        group: 'Actions',
        label: 'Open project settings',
        keywords: 'project settings git memory mcp',
        run: openProjectSettings,
      },
    ];
    return [...nav, ...switchProject, ...actions];
  }, [projects, slugs, goProject, setActiveView, openPreferences, openProjectSettings]);

  const rawCockpit: unknown = import.meta.env.VITE_CFACTORY_URL;
  const cockpitUrl = (
    typeof rawCockpit === 'string' && rawCockpit.trim()
      ? rawCockpit
      : 'https://cfactory.freundcloud.org.uk'
  ).replace(/\/+$/, '');
  const searchFleet = useCallback(async (q: string): Promise<PaletteTask[]> => {
    const res = await apiRequest<{
      results: { correlation_key: string; title: string | null; status: string | null }[];
    }>(`/search?q=${encodeURIComponent(q)}`);
    if (!res.success || !res.data) return [];
    return res.data.results.map((r) => ({
      id: r.correlation_key,
      title: r.title ?? `#${r.correlation_key}`,
      hint: r.status ?? undefined,
    }));
  }, []);

  const selectedProject = inSync ? urlProject : undefined;

  useEffect(() => {
    if (!selectedProject) setIsNewTaskDialogOpen(false);
  }, [selectedProject]);

  const switchingProjectName = useMemo(() => {
    if (!isSwitchingProject || !urlProjectId) return undefined;
    return projects.find((p) => p.id === urlProjectId)?.name;
  }, [isSwitchingProject, urlProjectId, projects]);

  useEffect(() => {
    void loadProjects().finally(() => {
      setProjectsLoaded(true);
    });
  }, [projectLoadAttempt]);
  useEffect(() => {
    void loadSettings();
  }, []);

  useEffect(() => {
    if (settings.onboardingCompleted !== false) return;

    const checkSetup = async () => {
      try {
        const [versionResult, authResult] = await Promise.all([
          window.API?.checkClaudeCodeVersion?.() ?? { success: false },
          window.API?.getAuthStatus?.() ?? { success: false },
        ]);

        const cliInstalled = versionResult.success && versionResult.data?.installed;
        const hasToken = authResult.success && authResult.data?.hasToken;

        if (cliInstalled && hasToken) {
          const { updateSettings } = useSettingsStore.getState();
          updateSettings({ onboardingCompleted: true });
          void window.API?.saveSettings?.({ onboardingCompleted: true });
        } else {
          setIsOnboardingOpen(true);
        }
      } catch {
        setIsOnboardingOpen(true);
      }
    };

    void checkSetup();
  }, [settings.onboardingCompleted]);

  const { t, i18n } = useTranslation(['navigation', 'settings', 'tasks', 'welcome']);
  useEffect(() => {
    if (settings.language && settings.language !== i18n.language) {
      void i18n.changeLanguage(settings.language);
    }
  }, [settings.language, i18n]);

  useEffect(() => {
    if (selectedProject?.id) {
      void loadTasks(selectedProject.id);
      setSelectedTaskId(null);
    } else {
      useTaskStore.getState().clearTasks();
    }
  }, [selectedProject?.id]);

  useEffect(() => {
    if (!isSwitchingProject) return;
    const timeout = setTimeout(() => {
      useProjectStore.getState().clearSwitchingState();
    }, 10_000);
    return () => clearTimeout(timeout);
  }, [isSwitchingProject]);

  useEffect(() => {
    const root = document.documentElement;

    const applyTheme = () => {
      if (settings.theme === 'dark') {
        root.classList.add('dark');
      } else if (settings.theme === 'light') {
        root.classList.remove('dark');
      } else {
        if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
          root.classList.add('dark');
        } else {
          root.classList.remove('dark');
        }
      }
    };

    applyTheme();

    try {
      localStorage.setItem('tfactory-theme', settings.theme ?? 'system');
    } catch {
      // localStorage may be unavailable
    }

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      if (settings.theme === 'system') {
        applyTheme();
      }
    };
    mediaQuery.addEventListener('change', handleChange);

    return () => {
      mediaQuery.removeEventListener('change', handleChange);
    };
  }, [settings.theme]);

  useEffect(() => {
    const root = document.documentElement;
    const scale = settings.uiScale ?? UI_SCALE_DEFAULT;
    const clampedScale = Math.max(UI_SCALE_MIN, Math.min(UI_SCALE_MAX, scale));
    root.setAttribute('data-ui-scale', clampedScale.toString());
  }, [settings.uiScale]);

  const handleAddProject = useCallback(() => {
    setIsAddProjectModalOpen(true);
  }, []);

  const handleProjectAdded = (project: Project, needsInit: boolean) => {
    console.log('[Web] Project added:', project.name, 'needs init:', needsInit);
    goProject(project.id);
  };

  const handleOpenInbuiltTerminal = useCallback(
    (id: string, cwd: string) => {
      void window.API.createTerminal({
        id,
        cwd,
        cols: 80,
        rows: 24,
      });
      setActiveView('terminals');
    },
    [setActiveView]
  );

  const { nav, sidebarHeader, openLogoutDialog, dialogs } = useTFactoryShellAdapter(urlProjectId ?? null);

  const shellLabels: AppShellLabels = useMemo(
    () => ({
      brand: t('navigation:shell.brand', 'TFactory'),
      portals: t('navigation:shell.portals', 'Factory portals'),
      portalNames: {
        plan: t('navigation:shell.portalNames.plan', 'Plan'),
        build: t('navigation:shell.portalNames.build', 'Build'),
        test: t('navigation:shell.portalNames.test', 'Test'),
        cockpit: t('navigation:shell.portalNames.cockpit', 'Cockpit'),
        meet: t('navigation:shell.portalNames.meet', 'Meet'),
      },
      navigation: t('navigation:shell.navigation', 'Project navigation'),
      openNavigation: t('navigation:shell.openNavigation', 'Open navigation'),
      closeNavigation: t('navigation:shell.closeNavigation', 'Close navigation'),
      expandSidebar: t('navigation:shell.expandSidebar', 'Expand sidebar'),
      collapseSidebar: t('navigation:shell.collapseSidebar', 'Collapse sidebar'),
      projectSettings: t('navigation:shell.projectSettings', 'Project Settings'),
      theme: t('navigation:shell.theme', 'Theme'),
      themeOptions: {
        light: t('settings:theme.light', 'Light'),
        dark: t('settings:theme.dark', 'Dark'),
        system: t('settings:theme.system', 'System'),
      },
      search: t('navigation:shell.search', 'Search'),
      searchShortcut: isMac ? '⌘K' : 'Ctrl+K',
      needsYou: (count: number) => t('navigation:shell.needsYou', { count, defaultValue: '{{count}} need you' }),
      userMenu: (name: string) => t('navigation:shell.userMenu', { name, defaultValue: 'Account menu for {{name}}' }),
      project: {
        project: t('navigation:sections.project', 'Project'),
        selectProject: t('navigation:shell.selectProject', 'Select project'),
        searchProjects: t('navigation:shell.searchProjects', 'Search projects…'),
        projects: t('navigation:shell.projects', 'Projects'),
        recentProjects: t('navigation:shell.recentProjects', 'Recent'),
        noProjects: t('navigation:shell.noProjects', 'No matching projects'),
        allProjects: t('navigation:shell.allProjects', 'All projects'),
        addProject: t('navigation:shell.addProject', 'Add project'),
      },
      pipeline: {
        pipeline: t('navigation:shell.pipeline', 'Pipeline stages'),
        clearFilter: t('navigation:shell.clearFilter', 'Clear filter'),
        failures: (count: number) => t('tasks:pipeline.failed', { count, defaultValue: '{{count}} failed' }),
        filterFailures: (stage) =>
          t('navigation:shell.filterFailures', {
            stage: stage.label,
            count: stage.failed,
            defaultValue: 'Filter {{stage}} failures ({{count}})',
          }),
      },
    }),
    [t]
  );

  const viewLabel = useMemo(() => {
    if (route.kind === 'preferences') return t('navigation:shell.preferences', 'Preferences');
    if (route.kind === 'projects') return t('navigation:shell.projects', 'Projects');
    if (route.kind === 'project' && route.view === 'settings') {
      return t('navigation:shell.projectSettings', 'Project Settings');
    }
    const found = nav.find((item) => item.id === activeView);
    if (found) return found.label;
    if (activeView === 'terminals') return t('navigation:items.terminals', 'Terminal');
    if (activeView === 'worktrees') return t('navigation:items.worktrees', 'Worktrees');
    if (activeView === 'insights') return 'Insights';
    return t('navigation:items.tests', 'Tests');
  }, [route, nav, activeView, t]);

  const currentTheme: 'light' | 'dark' | 'system' =
    settings.theme === 'light' || settings.theme === 'dark' ? settings.theme : 'system';

  const handleThemeChange = useCallback(
    (nextTheme: 'light' | 'dark' | 'system') => {
      updateStoreSettings({ theme: nextTheme });
      void saveSettings({ theme: nextTheme });
    },
    [updateStoreSettings]
  );

  const isSettingsPage =
    route.kind === 'preferences' || (route.kind === 'project' && route.view === 'settings');

  const stripContextValue = useMemo(() => ({ setStrip }), []);

  const primaryDisabled = !urlProject || !urlProject.autoBuildPath;
  const primaryDisabledReason = !urlProject
    ? t('settings:projectSettings.noProjectSelected.title', 'No Project Selected')
    : !urlProject.autoBuildPath
      ? t('navigation:messages.initializeToCreateTasks', 'Initialize TFactory to create tasks')
      : undefined;

  return (
    <ViewStateProvider>
      <TooltipProvider>
        <PipelineStripContext.Provider value={stripContextValue}>
          <AppShell
            portal="test"
            portalProject={urlSlug ? { slug: urlSlug, portals: ['plan', 'build', 'test'] } : undefined}
            nav={nav}
            activeNav={route.kind === 'project' ? route.view : pathname === '/tests' ? 'tfactory' : ''}
            onNavigate={(id) => {
              setActiveView(id as SidebarView);
            }}
            projects={switcherProjects}
            currentProject={urlSlug}
            projectSubtitle={urlProject?.path}
            onSelectProject={(slug) => {
              const id = [...slugs].find(([, v]) => v === slug)?.[0] ?? findProjectByRef(projects, slug)?.id;
              if (id) goProject(id, route.kind === 'project' ? route.view : undefined);
            }}
            onAllProjects={() => {
              void navigate('/projects');
            }}
            onAddProject={handleAddProject}
            view={viewLabel}
            projectSettings={
              urlProject
                ? {
                    active: route.kind === 'project' && route.view === 'settings',
                    onOpen: openProjectSettings,
                  }
                : undefined
            }
            primaryAction={{
              label: `+ ${t('navigation:actions.newTask', 'Task')}`,
              onClick: () => setIsNewTaskDialogOpen(true),
              disabled: primaryDisabled,
              disabledReason: primaryDisabledReason,
            }}
            strip={isSettingsPage ? undefined : strip}
            topbarStatus={
              <>
                <ClaudeCodeStatusBadge iconOnly onOpenOnboarding={() => setIsOnboardingOpen(true)} />
                <CLIToolStatusBadge iconOnly />
                <OpenAIEndpointsStatusBadge iconOnly />
                <RateLimitIndicator />
              </>
            }
            sidebarHeader={sidebarHeader}
            sidebarCollapsed={sidebarCollapsed}
            onSidebarCollapsedChange={handleSidebarCollapsedChange}
            user={{
              name: t('navigation:shell.userName', 'Account'),
              menu: [
                {
                  id: 'preferences',
                  label: t('navigation:shell.preferences', 'Preferences'),
                  onSelect: openPreferences,
                },
                {
                  id: 'help',
                  label: t('navigation:shell.helpDocs', 'Help & docs'),
                  onSelect: () => {
                    window.open('https://github.com/olafkfreund/TFactory#readme', '_blank', 'noopener,noreferrer');
                  },
                },
                {
                  id: 'logout',
                  label: t('navigation:actions.logout', 'Log Out'),
                  onSelect: openLogoutDialog,
                },
              ],
            }}
            theme={currentTheme}
            onTheme={handleThemeChange}
            onSearch={() => setPaletteOpen(true)}
            needsYou={{
              count: needsCount,
              onOpen: () => {
                window.location.href = cockpitUrl;
              },
            }}
            version={version}
            labels={shellLabels}
          >
            {route.kind === 'project' && urlProject && (
              /* TerminalGrid stays mounted across project switches to preserve xterm instances and PTY connections */
              <div className={activeView === 'terminals' ? 'h-full' : 'hidden'}>
                <TerminalGrid
                  projectPath={urlProject.path}
                  onNewTaskClick={() => setIsNewTaskDialogOpen(true)}
                  isActive={activeView === 'terminals'}
                />
              </div>
            )}
            {projectError && projectsLoaded && (
              <div role="alert" className="flex h-full flex-col items-center justify-center gap-2">
                <p>{projectError}</p>
                <button
                  onClick={() => {
                    setProjectsLoaded(false);
                    setProjectLoadAttempt((n) => n + 1);
                  }}
                >
                  {t('welcome:projectsPage.retry')}
                </button>
              </div>
            )}
            {route.kind === 'project' && !projectError && !urlProject && projectsLoaded && isBareSlugChooser(projects, route.ref) && (
              <div className="flex h-full flex-col items-center justify-center gap-2">
                <p>{t('welcome:projectsPage.chooseProject')}</p>
                {projectsForBareSlug(projects, route.ref).map((p) => (
                  <Link key={p.id} to={projectPath(slugs.get(p.id) ?? p.id)}>
                    {p.name} — {p.path}
                  </Link>
                ))}
              </div>
            )}
            {route.kind === 'project' && !projectError && !urlProject && projectsLoaded && !isBareSlugChooser(projects, route.ref) && (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
                <p>{t('welcome:projectsPage.notFound', { ref: route.ref })}</p>
                <Link to="/" className="text-sm underline">
                  {t('welcome:projectsPage.backHome')}
                </Link>
              </div>
            )}
            {selectedProject && (
              <>
                {activeView === 'tfactory' && (
                  <div className="h-full overflow-auto">
                    <TFactoryPortal key={selectedProject.id} projectId={selectedProject.id} />
                  </div>
                )}
                {activeView === 'editor' && <EditorPage projectPath={selectedProject.path} />}
                {activeView === 'worktrees' && <Worktrees projectId={selectedProject.id} />}
                {activeView === 'context' && <Context projectId={selectedProject.id} />}
                {activeView === 'github-issues' && (
                  <GitHubIssues
                    onOpenSettings={openProjectSettings}
                    onNavigateToTask={(taskId) => {
                      setSelectedTaskId(taskId);
                      setActiveView('tfactory');
                    }}
                  />
                )}
                {activeView === 'github-prs' && (
                  <GitHubPRs onOpenSettings={openProjectSettings} isActive={true} />
                )}
                {activeView === 'changelog' && <Changelog />}
                {activeView === 'insights' && (
                  <Insights projectId={selectedProject.id} onNavigate={setActiveView} />
                )}
                {activeView === 'agent-tools' && <AgentTools />}
                {activeView === 'skills' && <SkillsPage />}
                {activeView === 'cloud' && (
                  <div className="h-full overflow-auto p-6">
                    <CloudAssessmentPage />
                  </div>
                )}
                {activeView === 'visual-reports' && (
                  <div className="h-full overflow-auto p-6">
                    <VisualReportsPage />
                  </div>
                )}
                {activeView === 'settings' && (
                  <ProjectSettingsPage projectId={selectedProject.id} />
                )}
              </>
            )}
            {route.kind === 'preferences' && (
              <PreferencesPage onRerunWizard={() => setIsOnboardingOpen(true)} />
            )}
            {route.kind === 'projects' && !projectError && (
              <ProjectsPage
                projects={projects}
                slugs={slugs}
                onAdd={handleAddProject}
                onOpen={(p) => {
                  goProject(p.id);
                }}
              />
            )}
            {pathname === '/tests' && !projectError && <TFactoryPortal />}
            {route.kind === 'home' && pathname !== '/tests' && !projectError && (
              <WelcomeScreen
                projects={projects}
                onNewProject={handleAddProject}
                onOpenProject={handleAddProject}
                onSelectProject={(projectId) => {
                  goProject(projectId);
                }}
              />
            )}
          </AppShell>

          {dialogs}

          <ChatLauncher projectId={selectedProject?.id || ''} onNavigate={setActiveView} />

          <ProjectSwitchLoadingModal
            open={isSwitchingProject && !!urlProjectId}
            projectName={switchingProjectName}
          />

          <Toaster />

          <AddProjectModal
            open={isAddProjectModalOpen}
            onOpenChange={setIsAddProjectModalOpen}
            onProjectAdded={handleProjectAdded}
          />

          <CommandPalette
            open={paletteOpen}
            onClose={() => {
              setPaletteOpen(false);
            }}
            commands={paletteCommands}
            projects={breadcrumbProjects}
            onSwitchProject={(slug) => {
              void navigate(projectPath(slug));
            }}
            onSearch={searchFleet}
            onOpenTask={(taskItem) => {
              window.location.href = `${cockpitUrl}/?task=${encodeURIComponent(taskItem.id)}`;
            }}
          />

          {selectedProject && (
            <TaskCreationWizard
              projectId={selectedProject.id}
              open={isNewTaskDialogOpen}
              onOpenChange={setIsNewTaskDialogOpen}
              onCloudInfra={() => setIsCloudCheckOpen(true)}
            />
          )}

          <CloudCheckDialog open={isCloudCheckOpen} onOpenChange={setIsCloudCheckOpen} />

          <TaskDetailModal
            open={selectedTask !== null}
            task={selectedTask}
            onOpenChange={(open) => {
              if (!open) setSelectedTaskId(null);
            }}
            onSwitchToTerminals={() => setActiveView('terminals')}
            onOpenInbuiltTerminal={handleOpenInbuiltTerminal}
          />

          <OnboardingWizard
            open={isOnboardingOpen}
            onOpenChange={setIsOnboardingOpen}
            onOpenTaskCreator={() => setIsNewTaskDialogOpen(true)}
            onOpenSettings={openPreferences}
          />
        </PipelineStripContext.Provider>
      </TooltipProvider>
    </ViewStateProvider>
  );
}

export default function App() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const checkAuth = useAuthStore((state) => state.checkAuth);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void checkAuth().finally(() => {
      if (!cancelled) setAuthChecked(true);
    });
    return () => {
      cancelled = true;
    };
  }, [checkAuth]);

  if (!authChecked) return null;

  return (
    <Routes>
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />}
      />
      <Route
        path="/console/:projectId/:specId"
        element={isAuthenticated ? <LegacyConsoleRedirect /> : <RedirectToLogin />}
      />
      <Route
        path="/p/:slug/console/:specId"
        element={isAuthenticated ? <ConsolePage /> : <RedirectToLogin />}
      />
      <Route
        path="/*"
        element={isAuthenticated ? <AuthenticatedApp /> : <RedirectToLogin />}
      />
    </Routes>
  );
}
