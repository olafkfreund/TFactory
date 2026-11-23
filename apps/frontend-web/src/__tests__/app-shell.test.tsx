/** @vitest-environment jsdom */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import '../shared/i18n';
import App from '../App';
import { useAuthStore } from '../stores/auth-store';
import { useProjectStore } from '../stores/project-store';
import type { Project } from '../shared/types';

vi.mock('../hooks/useIpc', () => ({ useIpcListeners: () => undefined }));
vi.mock('../hooks/useSessionKeepAlive', () => ({ useSessionKeepAlive: () => undefined }));
vi.mock('../lib/api-client', () => ({
  apiRequest: vi.fn().mockResolvedValue({ success: true, data: { count: 2 } }),
  get: vi.fn().mockResolvedValue({ success: false }),
}));
vi.mock('../components/TerminalGrid', () => ({ TerminalGrid: () => null }));
vi.mock('../components/ChatLauncher', () => ({ ChatLauncher: () => null }));
vi.mock('../components/ui/toaster', () => ({ Toaster: () => null }));

const DEMO_PROJECT: Project = {
  id: 'id-alpha',
  name: 'Alpha',
  path: '/src/alpha',
  autoBuildPath: '.tfactory',
  settings: {
    model: 'claude-sonnet-4-5-20250929',
    memoryBackend: 'file',
    notifications: {
      onTaskComplete: true,
      onTaskFailed: true,
      onReviewNeeded: true,
      sound: false,
      emailEnabled: false,
    },
    graphitiMcpEnabled: false,
  },
  createdAt: new Date('2026-01-01'),
  updatedAt: new Date('2026-01-01'),
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  });
  useAuthStore.setState({ isAuthenticated: true, checkAuth: () => Promise.resolve(true) });
  useProjectStore.setState({
    projects: [],
    selectedProjectId: null,
    activeProjectId: null,
    openProjectIds: [],
    tabOrder: [],
  });
  const apiTarget: Record<string, unknown> = {
    getTabState: () => Promise.resolve({ success: true, data: {} }),
    saveTabState: () => Promise.resolve({ success: true }),
    getProjects: () => Promise.resolve({ success: true, data: [DEMO_PROJECT] }),
    getTasks: () => Promise.resolve({ success: true, data: [] }),
    getSettings: () => Promise.resolve({ success: true, data: { onboardingCompleted: true, theme: 'dark' } }),
    getAppVersion: () => Promise.resolve('0.14.1'),
    checkClaudeCodeVersion: () => Promise.resolve({ success: true, data: { installed: '1.0.0' } }),
    getAuthStatus: () => Promise.resolve({ success: true, data: { hasToken: true } }),
    checkGitStatus: () => Promise.resolve({ success: true, data: { isGitRepo: true, hasCommits: true } }),
    getProjectEnv: () => Promise.resolve({ success: true, data: { githubEnabled: true, gitProvider: 'github' } }),
  };
  (window as unknown as { API: unknown }).API = new Proxy(apiTarget, {
    get: (t, k: string) => t[k] ?? (() => Promise.resolve({ success: false })),
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      tasks: [
        {
          task_id: 'spec-1',
          project_id: 'id-alpha',
          spec_id: 'spec-1',
          status: 'triaged',
          phase: null,
          updated_at: '2026-05-28T10:00:00Z',
        },
      ],
      count: 1,
    }),
  }));
});

describe('TFactory adapted AppShell', () => {
  it('renders AppShell with portal="test", PipelineStrip, ProjectSwitcher, primary + Task action, and routes to Project Settings and Preferences', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/p/alpha']}>
        <App />
      </MemoryRouter>
    );

    const shell = await waitFor(() => {
      const el = container.querySelector('.f-shell');
      expect(el).toHaveAttribute('data-portal', 'test');
      return el!;
    });
    expect(shell).toBeInTheDocument();

    // PipelineStrip rendered in the shell above the page when on overview
    expect(await screen.findByRole('toolbar', { name: /Pipeline stages/i })).toBeInTheDocument();

    // Primary action "+ Task"
    expect(screen.getByRole('button', { name: '+ Task' })).toBeEnabled();

    // Project Settings pinned in sidebar navigates to /p/alpha/settings with General, Git, Memory, MCP tabs
    fireEvent.click(screen.getByRole('button', { name: 'Project Settings' }));
    expect(await screen.findByTestId('project-settings-page')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'General' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Git' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Memory' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'MCP' })).toBeInTheDocument();
    // Strip is hidden on settings pages
    expect(screen.queryByRole('toolbar', { name: /Pipeline stages/i })).toBeNull();

    // User menu Preferences navigates to /preferences
    fireEvent.click(screen.getByRole('button', { name: 'Preferences' }));
    expect(await screen.findByTestId('preferences-page')).toBeInTheDocument();
  });
});
