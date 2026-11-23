/** @vitest-environment jsdom */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import type { Project } from '../shared/types';

// Heavy children are stubs that print which project/view they got.
const xterm = vi.hoisted(() => ({ current: null as { disposed: boolean } | null }));
vi.mock('../components/TerminalGrid', async () => {
  const { useEffect, useState } = await import('react');
  return {
    TerminalGrid: (p: { projectPath?: string }) => {
      const [instance] = useState(() => ({ disposed: false }));
      useEffect(() => {
        xterm.current = instance;
        return () => { instance.disposed = true; };
      }, [instance]);
      return <div data-testid="terms">{p.projectPath}</div>;
    },
  };
});
vi.mock('../components/Worktrees', () => ({ Worktrees: () => null }));
vi.mock('../components/context/Context', () => ({ Context: () => null }));
vi.mock('../components/GitHubIssues', () => ({ GitHubIssues: (p: { onOpenSettings: () => void }) => <button onClick={p.onOpenSettings}>Issue settings</button> }));
vi.mock('../components/github-prs/GitHubPRs', () => ({ GitHubPRs: (p: { onOpenSettings: () => void }) => <button onClick={p.onOpenSettings}>PR settings</button> }));
vi.mock('../components/changelog/Changelog', () => ({ Changelog: () => null }));
vi.mock('../components/Insights', () => ({ Insights: () => <div data-testid="insights" /> }));
vi.mock('../components/ChatLauncher', () => ({ ChatLauncher: () => null }));
vi.mock('../components/AgentTools', () => ({ AgentTools: () => null }));
vi.mock('../components/SkillsPage', () => ({ SkillsPage: () => null }));
vi.mock('../components/settings/sections/CloudAssessmentPage', () => ({ CloudAssessmentPage: () => null }));
vi.mock('../components/settings/sections/VisualReportsPage', () => ({ VisualReportsPage: () => null }));
vi.mock('../components/WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="home" /> }));
vi.mock('../components/AddProjectModal', () => ({ AddProjectModal: () => null }));
vi.mock('../components/settings', () => ({
  ProjectSettingsPage: (p: { projectId: string | null }) => <output data-testid="settings-project">{p.projectId ?? 'none'}</output>,
  PreferencesPage: () => <div data-testid="preferences-page" />,
}));
vi.mock('../components/TaskCreationWizard', () => ({ TaskCreationWizard: (p: { projectId: string }) => <output data-testid="wizard-project">{p.projectId}</output> }));
vi.mock('../components/CloudCheckDialog', () => ({ CloudCheckDialog: () => null }));
vi.mock('../components/task-detail', () => ({ TaskDetailModal: () => null }));
vi.mock('../components/onboarding', () => ({ OnboardingWizard: (p: { onOpenSettings: () => void }) => <button onClick={p.onOpenSettings}>Onboarding settings</button> }));
vi.mock('../components/ProjectSwitchLoadingModal', () => ({ ProjectSwitchLoadingModal: () => null }));
vi.mock('../components/CommandPalette', () => ({ CommandPalette: (p: { open: boolean }) => p.open ? <div data-testid="palette" /> : null }));
vi.mock('../components/ui/toaster', () => ({ Toaster: () => null }));
vi.mock('../pages/EditorPage', () => ({ EditorPage: () => null }));
vi.mock('../pages/LoginPage', () => ({ LoginPage: () => <div data-testid="login" /> }));
vi.mock('../components/task-detail/AgentConsole', () => ({ AgentConsole: (p: { taskId: string }) => <div data-testid="console">{p.taskId}</div> }));
vi.mock('../hooks/useIpc', () => ({ useIpcListeners: () => undefined }));
vi.mock('../hooks/useSessionKeepAlive', () => ({ useSessionKeepAlive: () => undefined }));
vi.mock('../lib/api-client', () => ({
  apiRequest: vi.fn().mockResolvedValue({ success: false }),
  get: vi.fn().mockResolvedValue({ success: false }),
}));
vi.mock('../stores/auth-store', async () => {
  const { create } = await import('zustand');
  const useAuthStore = create(() => ({
    isAuthenticated: true,
    checkAuth: () => Promise.resolve(true),
    logout: () => undefined,
  }));
  return { useAuthStore };
});

import '../shared/i18n';
import App from '../App';
import { useProjectStore } from '../stores/project-store';
import { useTaskStore } from '../stores/task-store';
import { useAuthStore } from '../stores/auth-store';

const proj = (id: string, name: string, createdAt: string): Project =>
  ({ id, name, path: `/src/${id}`, autoBuildPath: '.a', createdAt: new Date(createdAt), updatedAt: new Date(createdAt) }) as unknown as Project;
const A = proj('id-alpha', 'Alpha', '2026-01-01');
const B = proj('id-beta', 'Beta', '2026-01-02');

let tasksFor: Record<string, { id: string; title: string; projectId: string }[]> = {};
function setApi(tab: Record<string, unknown>) {
  const target: Record<string, unknown> = {
    getTabState: () => Promise.resolve({ success: true, data: tab }),
    getProjects: () => Promise.resolve({ success: true, data: [A, B] }),
    saveTabState: () => Promise.resolve({ success: true }),
    getTasks: (id: string) => Promise.resolve({ success: true, data: tasksFor[id] ?? [] }),
    getSettings: () => Promise.resolve({ success: true, data: { onboardingCompleted: true, theme: 'dark' } }),
  };
  (window as unknown as { API: unknown }).API = new Proxy(
    target,
    { get: (t, k: string) => t[k] ?? (() => Promise.resolve({ success: false })) },
  );
}

let nav: ReturnType<typeof useNavigate>;
function Probe() { nav = useNavigate(); const { pathname, search } = useLocation(); return <output data-testid="location">{pathname + search}</output>; }
const renderAt = (path: string) =>
  render(<MemoryRouter initialEntries={[path]}><Probe /><App /></MemoryRouter>);

beforeEach(() => {
  useAuthStore.setState({ isAuthenticated: true });
  xterm.current = null;
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  });
  tasksFor = {
    'id-alpha': [{ id: 't1', title: 'alpha-task', projectId: 'id-alpha' }],
    'id-beta': [{ id: 't2', title: 'beta-task', projectId: 'id-beta' }],
  };
  useProjectStore.setState({ projects: [], selectedProjectId: null, activeProjectId: null, openProjectIds: [], tabOrder: [] });
  useTaskStore.setState({ tasks: [] });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
    const projectId = new URL(url, 'http://localhost').searchParams.get('project_id');
    const tasks = projectId ? (tasksFor[projectId] ?? []).map((task) => ({
      task_id: task.id, project_id: projectId, spec_id: task.id,
      status: 'triaged', phase: null, updated_at: '2026-01-01',
    })) : [];
    return { ok: true, json: async () => ({ tasks, count: tasks.length }) };
  }));
});

describe('URL-scoped projects', () => {
  it('URL beats the saved active tab and last-selected project', async () => {
    localStorage.setItem('lastSelectedProjectId', 'id-beta');
    setApi({ openProjectIds: ['id-beta'], activeProjectId: 'id-beta', tabOrder: ['id-beta'] });
    renderAt('/p/alpha');
    await screen.findByTestId('task-row-t1');
    expect(useProjectStore.getState().activeProjectId).toBe('id-alpha');
    expect(useProjectStore.getState().openProjectIds).toContain('id-beta');
  });

  it('rewrites an id in the URL to the canonical slug', async () => {
    setApi({});
    renderAt('/p/id-beta');
    await screen.findByTestId('task-row-t2');
    await waitFor(() => { expect(screen.getByTestId('terms')).toHaveTextContent('/src/id-beta'); });
    await waitFor(() => { expect(screen.getByTestId('location')).toHaveTextContent('/p/beta'); });
  });

  it('opens different URL projects with the same saved tab state', async () => {
    setApi({ openProjectIds: ['id-beta'], activeProjectId: 'id-beta', tabOrder: ['id-beta'] });
    const first = renderAt('/p/alpha');
    await screen.findByTestId('task-row-t1');
    expect(screen.getByTestId('location')).toHaveTextContent('/p/alpha');
    first.unmount();
    useProjectStore.setState({ projects: [], selectedProjectId: null, activeProjectId: null, openProjectIds: [], tabOrder: [] });
    useTaskStore.getState().clearTasks();
    renderAt('/p/beta');
    await screen.findByTestId('task-row-t2');
    expect(screen.getByTestId('location')).toHaveTextContent('/p/beta');
  });

  it('shows not found for an unknown slug and never mounts views', async () => {
    setApi({});
    renderAt('/p/nope');
    expect(await screen.findByText(/Project not found/)).toBeInTheDocument();
    expect(screen.queryByTestId('tfactory-portal')).toBeNull();
  });

  it('shows a chooser for a collision and never promotes its bare slug', async () => {
    const first = proj('id-first', 'Demo', '2026-01-01');
    const second = proj('id-second', 'demo', '2026-02-01');
    setApi({});
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve({ success: true, data: [second, first] });
    renderAt('/p/DEMO');
    expect(await screen.findByText('Choose a project')).toBeInTheDocument();
    expect(within(screen.getByRole('main')).getAllByRole('link')).toHaveLength(2);
    expect(screen.queryByTestId('tfactory-portal')).toBeNull();
    act(() => { useProjectStore.setState({ projects: [second] }); });
    expect(screen.getByText('Choose a project')).toBeInTheDocument();
    expect(within(screen.getByRole('main')).getAllByRole('link')).toHaveLength(1);
  });

  it('keeps a bare collision slug as a chooser after one project is removed', async () => {
    const first = proj('id-first', 'Demo', '2026-01-01');
    const next = proj('id-next', 'Demo', '2026-02-01');
    setApi({});
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve({ success: true, data: [first, next] });
    renderAt('/p/demo');
    expect(await screen.findByText('Choose a project')).toBeInTheDocument();
    act(() => { useProjectStore.setState({ projects: [next] }); });
    expect(screen.getByText('Choose a project')).toBeInTheDocument();
    expect(screen.queryByTestId('tfactory-portal')).toBeNull();
  });

  it('back/forward style navigation re-syncs and never shows the other project tasks', async () => {
    setApi({});
    renderAt('/p/alpha');
    await screen.findByTestId('task-row-t1');
    act(() => { void nav('/p/beta'); });
    await screen.findByTestId('task-row-t2');
    expect(screen.queryByTestId('task-row-t1')).toBeNull();
    act(() => { void nav(-1); });
    await screen.findByTestId('task-row-t1');
    act(() => { void nav(1); });
    await screen.findByTestId('task-row-t2');
    expect(useProjectStore.getState().activeProjectId).toBe('id-beta');
  });

  it('keeps the xterm instance alive through project and global route switches', async () => {
    setApi({});
    renderAt('/p/alpha');
    await waitFor(() => { expect(screen.getByTestId('terms')).toHaveTextContent('/src/id-alpha'); });
    const instance = xterm.current;
    act(() => { void nav('/p/beta'); });
    await waitFor(() => { expect(screen.getByTestId('terms')).toHaveTextContent('/src/id-beta'); });
    act(() => { void nav('/preferences'); });
    await screen.findByTestId('preferences-page');
    act(() => { void nav('/projects'); });
    await screen.findByRole('heading', { name: 'Projects' });
    expect(xterm.current).toBe(instance);
    expect(instance?.disposed).toBe(false);
  });

  it('switcher searches, uses arrows and Enter, preserves view and task scope, and Escape cancels', async () => {
    setApi({});
    renderAt('/p/alpha/insights');
    await screen.findByTestId('insights');
    fireEvent.click(screen.getByRole('button', { name: /Project.*Alpha/ }));
    const search = screen.getByRole('combobox', { name: /Search projects/i });
    fireEvent.change(search, { target: { value: 'beta' } });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/p/beta/insights'));
    await waitFor(() => expect(useTaskStore.getState().tasks.map((task) => task.projectId)).toEqual(['id-beta']));
    expect(screen.getByTestId('terms')).toHaveTextContent('/src/id-beta');
    fireEvent.click(screen.getByRole('button', { name: /Project.*Beta/ }));
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' });
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByTestId('location')).toHaveTextContent('/p/beta/insights');
  });

  it('numeric shortcuts follow registry order, skip editable fields, and Ctrl+K opens the palette', async () => {
    setApi({});
    renderAt('/p/beta/insights');
    await screen.findByTestId('insights');
    fireEvent.keyDown(window, { key: '1', ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/p/alpha/insights'));
    fireEvent.click(screen.getByRole('button', { name: /Project.*Alpha/ }));
    const search = screen.getByRole('combobox');
    fireEvent.keyDown(search, { key: '2', ctrlKey: true });
    expect(screen.getByTestId('location')).toHaveTextContent('/p/alpha/insights');
    fireEvent.keyDown(search, { key: 'Escape' });
    fireEvent.keyDown(window, { key: '2', metaKey: true });
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/p/beta/insights'));
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(screen.getByTestId('palette')).toBeInTheDocument();
  });

  it('maps Ctrl/⌘+1–9 to all nine registry slots', async () => {
    const nine = Array.from({ length: 9 }, (_, i) => proj(`id-${i + 1}`, `Project${i + 1}`, `2026-01-${String(i + 1).padStart(2, '0')}`));
    setApi({});
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve({ success: true, data: nine });
    renderAt('/p/project1/insights');
    await screen.findByTestId('insights');
    for (let i = 1; i <= 9; i++) {
      fireEvent.keyDown(window, { key: String(i), ctrlKey: i % 2 === 1, metaKey: i % 2 === 0 });
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(`/p/project${i}/insights`));
    }
  });

  it('view segment selects the view', async () => {
    setApi({});
    renderAt('/p/alpha/insights');
    expect(await screen.findByTestId('insights')).toBeInTheDocument();
  });

  it('Tests click from another view keeps the project', async () => {
    setApi({});
    renderAt('/p/alpha/insights');
    await screen.findByTestId('insights');
    fireEvent.click(screen.getByRole('button', { name: 'Tests' }));
    expect(await screen.findByTestId('task-row-t1')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/p/alpha');
    expect(screen.queryByTestId('task-row-t2')).toBeNull();
    expect(fetch).toHaveBeenCalledWith('/api/tfactory/tasks?project_id=id-alpha', expect.anything());
  });

  it('project switch resets an open detail view', async () => {
    setApi({});
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      const projectId = new URL(url, 'http://localhost').searchParams.get('project_id');
      const specId = projectId === 'id-beta' ? 't2' : 't1';
      const body = url.includes(`/${specId}?`)
        ? { task_id: specId, project_id: projectId, spec_id: specId, status_json: { status: 'triaged' }, artefacts: {} }
        : { tasks: [{ task_id: specId, project_id: projectId, spec_id: specId, status: 'triaged', phase: null, updated_at: '2026-01-01' }], count: 1 };
      return { ok: true, json: async () => body };
    }));
    renderAt('/p/alpha');
    await screen.findByTestId('task-row-t1');
    fireEvent.click(screen.getByRole('button', { name: 't1' }));
    expect(await screen.findByTestId('tfactory-task-detail')).toBeInTheDocument();
    act(() => { void nav('/p/beta'); });
    expect(await screen.findByTestId('task-row-t2')).toBeInTheDocument();
    expect(screen.queryByTestId('tfactory-task-detail')).toBeNull();
  });

  it('/ shows My projects, /projects the Projects page', async () => {
    setApi({});
    const r = renderAt('/');
    expect(await screen.findByTestId('home')).toBeInTheDocument();
    r.unmount();
    renderAt('/projects');
    expect(await screen.findByRole('heading', { name: 'Projects' })).toBeInTheDocument();
    expect(document.querySelectorAll('main')).toHaveLength(1);
  });

  it('a deep link opened logged out returns to the same URL after login', async () => {
    setApi({});
    useAuthStore.setState({ isAuthenticated: false });
    const r = renderAt('/p/beta/insights');
    expect(await screen.findByTestId('login')).toBeInTheDocument();
    expect(sessionStorage.getItem('tf-return-to')).toBe('/p/beta/insights');
    r.unmount();
    useAuthStore.setState({ isAuthenticated: true });
    renderAt('/');
    expect(await screen.findByTestId('insights')).toBeInTheDocument();
    expect(sessionStorage.getItem('tf-return-to')).toBeNull();
  });

  it('legacy console link redirects to /p/:slug/console/:specId', async () => {
    setApi({});
    renderAt('/console/id-alpha/001-auth');
    expect(await screen.findByTestId('console')).toHaveTextContent('id-alpha:001-auth');
  });

  it('keeps the real workspace queue on the global route', async () => {
    setApi({});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({
      tasks: [
        { task_id: 'id-alpha:001', project_id: 'id-alpha', spec_id: '001', status: 'triaged', phase: null, updated_at: '2026-01-01' },
        { task_id: 'id-beta:002', project_id: 'id-beta', spec_id: '002', status: 'triaged', phase: null, updated_at: '2026-01-01' },
      ], count: 2,
    }) }));
    renderAt('/p/alpha');
    await screen.findByTestId('task-row-001');
    act(() => { void nav('/tests'); });
    expect(await screen.findByTestId('tfactory-portal')).toBeInTheDocument();
    expect(await screen.findByTestId('task-row-001')).toBeInTheDocument();
    expect(screen.getByTestId('task-row-002')).toBeInTheDocument();
    expect(screen.queryByTestId('task-row-t1')).toBeNull();
  });

  it('drops project-only consumers when leaving the project URL', async () => {
    setApi({ openProjectIds: ['id-beta'], activeProjectId: 'id-beta', tabOrder: ['id-beta'] });
    renderAt('/p/alpha');
    await screen.findByTestId('task-row-t1');
    expect(screen.getByTestId('wizard-project')).toHaveTextContent('id-alpha');
    act(() => { void nav('/projects'); });
    expect(screen.getByRole('button', { name: 'Files' })).toBeDisabled();
    expect(screen.queryByTestId('settings-project')).toBeNull();
    expect(screen.queryByTestId('wizard-project')).toBeNull();
  });

  it('routes /p/:slug/settings and /preferences to the dedicated settings pages', async () => {
    setApi({});
    renderAt('/p/alpha/settings');
    expect(await screen.findByTestId('settings-project')).toHaveTextContent('id-alpha');
    act(() => { void nav('/preferences'); });
    expect(await screen.findByTestId('preferences-page')).toBeInTheDocument();
  });

  it('opens Preferences directly and leaves fleet Tests enabled from global pages', async () => {
    setApi({});
    renderAt('/preferences');
    expect(await screen.findByTestId('preferences-page')).toBeInTheDocument();
    const tests = screen.getByRole('button', { name: 'Tests' });
    expect(tests).toBeEnabled();
    fireEvent.click(tests);
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/tests'));
  });

  it('keeps Settings in an ambiguous project chooser link', async () => {
    const first = proj('id-first', 'Demo', '2026-01-01');
    const second = proj('id-second', 'demo', '2026-02-01');
    setApi({});
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve({ success: true, data: [first, second] });
    renderAt('/p/demo/settings');
    expect(await screen.findByText('Choose a project')).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('main')).getAllByRole('link')[0]);
    expect(await screen.findByTestId('settings-project')).toHaveTextContent('id-first');
    expect(screen.getByTestId('location')).toHaveTextContent('/settings');
  });

  it.each([
    ['/p/alpha/github-issues', 'Issue settings', '/p/alpha/settings'],
    ['/p/alpha/github-prs', 'PR settings', '/p/alpha/settings'],
    ['/p/alpha', 'Onboarding settings', '/preferences'],
  ])('routes %s entry point to its settings page', async (path, label, destination) => {
    setApi({});
    renderAt(path);
    fireEvent.click(await screen.findByRole('button', { name: label }));
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(destination));
  });

  it('shows fetch failure with retry instead of project not found', async () => {
    setApi({});
    let calls = 0;
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve(
      ++calls === 1 ? { success: false, error: 'Network down' } : { success: true, data: [A, B] },
    );
    renderAt('/p/alpha');
    expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
    expect(screen.queryByText(/Project not found/)).toBeNull();
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByTestId('tfactory-portal')).toBeInTheDocument();
  });

  it('shows fetch failure on the projects index too', async () => {
    setApi({});
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve({ success: false, error: 'Index network down' });
    renderAt('/projects');
    expect(await screen.findByRole('alert')).toHaveTextContent('Index network down');
    expect(screen.queryByRole('heading', { name: 'Projects' })).toBeNull();
  });

  it('waits for console lookup and rejects unknown projects', async () => {
    setApi({});
    let resolve: ((value: unknown) => void) | undefined;
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => new Promise((r) => { resolve = r; });
    renderAt('/p/alpha/console/001');
    expect(screen.queryByTestId('console')).toBeNull();
    await waitFor(() => { expect(resolve).toBeDefined(); });
    await act(async () => { resolve?.({ success: true, data: [A, B] }); });
    expect(await screen.findByTestId('console')).toHaveTextContent('id-alpha:001');
    act(() => { void nav('/p/missing/console/001'); });
    expect(await screen.findByText(/Project not found/)).toBeInTheDocument();
    expect(screen.queryByTestId('console')).toBeNull();
  });

  it('retries a failed standalone console project fetch', async () => {
    setApi({});
    let calls = 0;
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve(
      ++calls === 1 ? { success: false, error: 'Console network down' } : { success: true, data: [A, B] },
    );
    renderAt('/p/alpha/console/001');
    expect(await screen.findByRole('alert')).toHaveTextContent('Console network down');
    expect(screen.queryByTestId('console')).toBeNull();
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByTestId('console')).toHaveTextContent('id-alpha:001');
  });

  it('legacy console resolves IDs first and preserves search', async () => {
    const collision = { ...A, slug: 'id-beta' };
    setApi({});
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = () => Promise.resolve({ success: true, data: [collision, B] });
    renderAt('/console/id-beta/001?tail=1');
    expect(await screen.findByTestId('console')).toHaveTextContent('id-beta:001');
    expect(screen.getByTestId('location')).toHaveTextContent('?tail=1');
  });

  it('clearing tasks invalidates a pending request on a route change', async () => {
    setApi({});
    let resolve: (value: unknown) => void = () => undefined;
    (window.API as unknown as { getTasks: (id: string) => Promise<unknown> }).getTasks = () => new Promise((r) => { resolve = r; });
    renderAt('/p/alpha');
    await screen.findByTestId('task-row-t1');
    act(() => { void nav('/projects'); });
    await act(async () => { resolve({ success: true, data: tasksFor['id-alpha'] }); });
    expect(useTaskStore.getState().tasks).toEqual([]);
  });

  it('an old response for project A cannot overwrite project B tasks', async () => {
    const { loadTasks } = await import('../stores/task-store');
    let resolveA: (v: unknown) => void = () => undefined;
    const api = { getTasks: (id: string) => id === 'id-alpha' ? new Promise((r) => { resolveA = r; }) : Promise.resolve({ success: true, data: tasksFor[id] }) };
    (window as unknown as { API: unknown }).API = api;
    const pa = loadTasks('id-alpha');
    await loadTasks('id-beta');
    resolveA({ success: true, data: tasksFor['id-alpha'] });
    await pa;
    expect(useTaskStore.getState().tasks.map((t) => t.title)).toEqual(['beta-task']);
  });
});
