/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Project } from '../../shared/types';
import '../../shared/i18n';
import { WelcomeScreen } from '../WelcomeScreen';

describe('WelcomeScreen', () => {
  it('shows repoLabel instead of pod path in Recent Projects', () => {
    const projects: Project[] = [
      {
        id: 'proj-1',
        name: 'aifactory-demo',
        path: '/data/workspaces/olafkfreund-aifactory-demo',
        repoLabel: 'olafkfreund/aifactory-demo',
        autoBuildPath: '.tfactory',
        createdAt: new Date('2026-01-01T00:00:00Z'),
        updatedAt: new Date('2026-01-02T00:00:00Z'),
      } as Project,
      {
        id: 'proj-2',
        name: 'local-only',
        path: '/data/workspaces/local-only',
        repoLabel: null,
        autoBuildPath: '',
        createdAt: new Date('2026-01-01T00:00:00Z'),
        updatedAt: new Date('2026-01-01T12:00:00Z'),
      } as Project,
    ];

    render(
      <WelcomeScreen
        projects={projects}
        onNewProject={vi.fn()}
        onOpenProject={vi.fn()}
        onSelectProject={vi.fn()}
      />,
    );

    expect(screen.getByText('olafkfreund/aifactory-demo')).toBeInTheDocument();
    expect(screen.queryByText('/data/workspaces/olafkfreund-aifactory-demo')).toBeNull();
    expect(screen.queryByText('/data/workspaces/local-only')).toBeNull();
  });
});
