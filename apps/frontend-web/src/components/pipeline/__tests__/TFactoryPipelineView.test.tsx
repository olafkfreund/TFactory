/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { TFactoryPipelineView } from '../TFactoryPipelineView';
import '../../../shared/i18n';

vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });

const response = (project: string) => ({ ok: true, json: async () => ({ tasks: [{
  task_id: project, project_id: project, spec_id: project,
  status: 'triaged', phase: null, updated_at: '2026-05-28T10:00:00Z',
}], count: 1 }) });

describe('TFactoryPipelineView polling', () => {
  it('ignores an old project poll after the project changes', async () => {
    let resolveOldPoll: ((value: ReturnType<typeof response>) => void) | undefined;
    let oldCalls = 0;
    const fetchFn = vi.fn().mockImplementation((url: string) => {
      if (url.includes('project_id=alpha')) {
        if (++oldCalls === 1) return Promise.resolve(response('alpha'));
        return new Promise((resolve) => { resolveOldPoll = resolve; });
      }
      return Promise.resolve(response('beta'));
    }) as unknown as typeof fetch;
    const view = render(<TFactoryPipelineView projectId="alpha" fetchFn={fetchFn} pollMs={20} onSelectTask={() => {}} />);
    await screen.findByTestId('task-row-alpha');
    await waitFor(() => expect(resolveOldPoll).toBeDefined());
    view.rerender(<TFactoryPipelineView projectId="beta" fetchFn={fetchFn} pollMs={0} onSelectTask={() => {}} />);
    await screen.findByTestId('task-row-beta');
    await act(async () => { resolveOldPoll?.(response('alpha')); });
    await waitFor(() => expect(screen.queryByTestId('task-row-alpha')).toBeNull());
    expect(screen.getByTestId('task-row-beta')).toBeInTheDocument();
  });
});
