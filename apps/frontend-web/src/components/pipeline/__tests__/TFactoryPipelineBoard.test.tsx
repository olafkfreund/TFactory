/**
 * @vitest-environment jsdom
 *
 * Terminal badges on the pipeline board (#1367).
 */

import { describe, it, expect, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { TFactoryPipelineBoard } from '../TFactoryPipelineBoard';
import { TFactoryPipelineView } from '../TFactoryPipelineView';
import i18n from '../../../shared/i18n';

vi.stubGlobal('ResizeObserver', class {
  observe() {}
  disconnect() {}
});

const task = (status: string, phase: string) => ({
  task_id: 't1',
  project_id: 'demo',
  spec_id: 't1',
  status,
  phase,
  updated_at: '2026-05-28T10:00:00+00:00',
});

const renderBoard = (t: ReturnType<typeof task>) =>
  render(<TFactoryPipelineBoard tasks={[t]} onSelectTask={() => {}} />);

describe('TFactoryPipelineBoard terminal badges', () => {
  it('keeps a reaped failed run outside the stage columns', () => {
    const { container } = renderBoard(task('failed', 'verify_job_reaped'));
    expect(container.querySelectorAll('.pl-speccard')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Failed (1)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Failed (1)' }));
    expect(screen.getByTestId('failed-runs')).toHaveTextContent('t1');
  });

  it('shows generated_empty as done with the never-run stages pending', () => {
    const { container } = renderBoard(task('generated_empty', 'gen_functional'));
    expect(screen.getAllByLabelText('Triaged')).toHaveLength(1);
    const states = [...container.querySelectorAll('.pl-chip')].map((c) => c.getAttribute('data-state'));
    expect(states).toEqual(['done', 'done', 'pending', 'pending']);
  });

  it('keeps failed and dismissed classification after a poll', () => {
    const failed = { ...task('stalled', 'executor'), dismissed: true };
    const view = render(<TFactoryPipelineBoard tasks={[failed]} onSelectTask={() => {}} />);
    expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Show dismissed'));
    expect(screen.getByRole('button', { name: 'Failed (1)' })).toBeInTheDocument();
    view.rerender(<TFactoryPipelineBoard tasks={[{ ...failed }]} onSelectTask={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Failed (1)' }));
    expect(screen.getByTestId('failed-runs')).toHaveTextContent('t1');
    expect(view.container.querySelectorAll('.pl-col .pl-speccard')).toHaveLength(0);
  });

  it('reruns a failed card in its project and moves it to Plan', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ planner_scheduled: false, planner_reason: 'auto_plan_disabled' }) }) as unknown as typeof fetch;
    const view = render(<TFactoryPipelineBoard tasks={[task('failed', 'executor')]} onSelectTask={() => {}} fetchFn={fetchFn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Failed (1)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Re-run' }));
    await waitFor(() => expect(view.container.querySelectorAll('.pl-col')[0]).toHaveTextContent('t1'));
    expect(fetchFn).toHaveBeenCalledWith('/api/tfactory/tasks/t1/rerun?project_id=demo', expect.objectContaining({ method: 'POST' }));
    expect(view.container.querySelector('.pl-col .pl-speccard')).not.toHaveTextContent('executor');
    expect(screen.getByRole('status')).toHaveTextContent('Automatic planning is disabled');
  });

  it('shows a new failure after a rerun and a successful poll', async () => {
    let polls = 0;
    const fetchFn = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => ({
      ok: true,
      json: async () => init?.method === 'POST'
        ? { planner_scheduled: true }
        : { tasks: [polls++ === 0 ? task('failed', 'executor') : polls === 2
          ? { ...task('planning', 'planner'), updated_at: '2026-05-28T10:01:00+00:00' }
          : { ...task('failed', 'executor'), updated_at: '2026-05-28T10:02:00+00:00' }], count: 1 },
    })) as unknown as typeof fetch;
    render(<TFactoryPipelineView projectId="demo" fetchFn={fetchFn} pollMs={200} onSelectTask={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Failed (1)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Re-run' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('tfactory-pipeline')).toHaveTextContent('planning'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Failed (1)' })).toBeInTheDocument());
    expect(screen.getByTestId('failed-runs')).toHaveTextContent('t1');
    expect(screen.getByRole('button', { name: 'Re-run' })).toBeInTheDocument();
  });

  it.each([
    ['checkout_failed', 'Source checkout failed; planner was not scheduled'],
    ['scheduler_failed', 'Planner scheduling failed'],
    ['auto_plan_disabled', 'Automatic planning is disabled'],
  ])('localizes planner reason %s', async (reason, message) => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ planner_scheduled: false, planner_reason: reason }) }) as unknown as typeof fetch;
    render(<TFactoryPipelineBoard tasks={[task('failed', 'executor')]} onSelectTask={() => {}} fetchFn={fetchFn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Failed (1)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Re-run' }));
    expect(await screen.findByRole('status')).toHaveTextContent(message);
  });

  it.each([
    ['fr', 'scheduler_failed', 'Le lancement de la planification a échoué'],
    ['pt-BR', 'checkout_failed', 'Falha ao obter o código-fonte; o planejador não foi agendado'],
  ])('shows planner reason in %s', async (language, reason, message) => {
    await i18n.changeLanguage(language);
    try {
      const fetchFn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ planner_scheduled: false, planner_reason: reason }) }) as unknown as typeof fetch;
      render(<TFactoryPipelineBoard tasks={[task('failed', 'executor')]} onSelectTask={() => {}} fetchFn={fetchFn} />);
      fireEvent.click(screen.getByRole('button', { name: i18n.t('tasks:pipeline.failed', { count: 1 }) }));
      fireEvent.click(screen.getByRole('button', { name: i18n.t('tasks:pipeline.rerun') }));
      expect(await screen.findByRole('status')).toHaveTextContent(message);
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it('dismisses a failed card and keeps it hidden after a stale poll', async () => {
    const failed = task('failed', 'executor');
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true, dismissed: true }) }) as unknown as typeof fetch;
    const view = render(<TFactoryPipelineBoard tasks={[failed]} onSelectTask={() => {}} fetchFn={fetchFn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Failed (1)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument());
    expect(fetchFn).toHaveBeenCalledWith('/api/tfactory/tasks/t1/dismiss?project_id=demo', expect.objectContaining({ method: 'POST' }));
    view.rerender(<TFactoryPipelineBoard tasks={[{ ...failed }]} onSelectTask={() => {}} fetchFn={fetchFn} />);
    expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument();
  });
});
