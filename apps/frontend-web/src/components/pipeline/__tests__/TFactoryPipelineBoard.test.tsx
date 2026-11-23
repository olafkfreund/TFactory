/**
 * @vitest-environment jsdom
 *
 * Pipeline board with shared WorkColumn, WorkCard and PipelineStrip (#3876).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
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

beforeEach(() => {
  localStorage.clear();
});

describe('TFactoryPipelineBoard shared WorkColumn and WorkCard', () => {
  it('keeps a reaped failed run outside the stage columns and renders WorkCard in failed-runs', () => {
    const { container } = renderBoard(task('failed', 'verify_job_reaped'));
    expect(container.querySelectorAll('.f-column .f-card')).toHaveLength(0);
    expect(screen.getAllByRole('button', { name: 'Failed (1)' }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: 'Failed (1)' })[0]);
    const failedRuns = screen.getByTestId('failed-runs');
    expect(failedRuns).toHaveTextContent('t1');
    expect(failedRuns).toHaveTextContent('verify_job_reaped');
  });

  it('collapses done/triaged cards by default and shows phase states when expanded', () => {
    const { container } = renderBoard(task('generated_empty', 'gen_functional'));
    const card = container.querySelector('.f-card');
    expect(card).toHaveClass('f-card--collapsed');
    fireEvent.click(screen.getByRole('button', { name: 'Expand t1' }));
    expect(container.querySelector('.f-card')).toHaveClass('f-card--expanded');
    const states = [...container.querySelectorAll('.f-card__phase')].map((c) => {
      if (c.classList.contains('f-card__phase--done')) return 'done';
      if (c.classList.contains('f-card__phase--active')) return 'active';
      if (c.classList.contains('f-card__phase--failed')) return 'failed';
      return 'pending';
    });
    expect(states).toEqual(['done', 'done', 'pending', 'pending']);
  });

  it('filters columns and failed runs from the PipelineStrip toolbar', () => {
    const { container } = render(
      <TFactoryPipelineBoard
        tasks={[
          task('planning', 'planner'),
          { ...task('failed', 'executor'), spec_id: 't-fail', task_id: 't-fail' },
        ]}
        onSelectTask={() => {}}
      />
    );
    expect(container.querySelectorAll('.f-column')).toHaveLength(4);
    const toolbar = screen.getByRole('toolbar', { name: /Pipeline stages/i });
    const planStageBtn = toolbar.querySelectorAll('.f-strip__node')[0];
    fireEvent.click(planStageBtn);
    expect(container.querySelectorAll('.f-column')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: /Clear filter/i }));
    expect(container.querySelectorAll('.f-column')).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: /Filter Execute failures/i }));
    expect(screen.getByTestId('failed-runs')).toHaveTextContent('t-fail');
  });

  it('keeps failed and dismissed classification after a poll', () => {
    const failed = { ...task('stalled', 'executor'), dismissed: true };
    const view = render(<TFactoryPipelineBoard tasks={[failed]} onSelectTask={() => {}} />);
    expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Show dismissed'));
    expect(screen.getAllByRole('button', { name: 'Failed (1)' }).length).toBeGreaterThan(0);
    view.rerender(<TFactoryPipelineBoard tasks={[{ ...failed }]} onSelectTask={() => {}} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Failed (1)' })[0]);
    expect(screen.getByTestId('failed-runs')).toHaveTextContent('t1');
    expect(view.container.querySelectorAll('.f-column .f-card')).toHaveLength(0);
  });

  it('reruns a failed card in its project and moves it to Plan', async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ planner_scheduled: false, planner_reason: 'auto_plan_disabled' }),
    }) as unknown as typeof fetch;
    const view = render(<TFactoryPipelineBoard tasks={[task('failed', 'executor')]} onSelectTask={() => {}} fetchFn={fetchFn} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Failed (1)' })[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Re-run' }));
    await waitFor(() => expect(view.container.querySelectorAll('.f-column')[0]).toHaveTextContent('t1'));
    expect(fetchFn).toHaveBeenCalledWith('/api/tfactory/tasks/t1/rerun?project_id=demo', expect.objectContaining({ method: 'POST' }));
    expect(view.container.querySelector('.f-column .f-card')).not.toHaveTextContent('executor');
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
    const failedBtns = await screen.findAllByRole('button', { name: 'Failed (1)' });
    fireEvent.click(failedBtns[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Re-run' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('tfactory-pipeline')).toHaveTextContent('planning'));
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Failed (1)' }).length).toBeGreaterThan(0));
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
    fireEvent.click(screen.getAllByRole('button', { name: 'Failed (1)' })[0]);
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
      fireEvent.click(screen.getAllByRole('button', { name: i18n.t('tasks:pipeline.failed', { count: 1 }) })[0]);
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
    fireEvent.click(screen.getAllByRole('button', { name: 'Failed (1)' })[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument());
    expect(fetchFn).toHaveBeenCalledWith('/api/tfactory/tasks/t1/dismiss?project_id=demo', expect.objectContaining({ method: 'POST' }));
    view.rerender(<TFactoryPipelineBoard tasks={[{ ...failed }]} onSelectTask={() => {}} fetchFn={fetchFn} />);
    expect(screen.getByRole('button', { name: 'Failed (0)' })).toBeInTheDocument();
  });
});
