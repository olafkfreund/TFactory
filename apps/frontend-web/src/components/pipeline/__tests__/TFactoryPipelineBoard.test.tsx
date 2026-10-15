/**
 * @vitest-environment jsdom
 *
 * Terminal badges on the pipeline board (#1367).
 */

import { describe, it, expect, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';

import { TFactoryPipelineBoard } from '../TFactoryPipelineBoard';

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
  it('shows a reaped failed run as Failed in the Report column', () => {
    const { container } = renderBoard(task('failed', 'verify_job_reaped'));
    expect(screen.getAllByLabelText('Failed')).toHaveLength(1);
    const report = container.querySelectorAll('.pl-col')[3];
    expect(report.querySelector('.pl-card-fail')).not.toBeNull();
  });

  it('shows generated_empty as done with the never-run stages pending', () => {
    const { container } = renderBoard(task('generated_empty', 'gen_functional'));
    expect(screen.getAllByLabelText('Triaged')).toHaveLength(1);
    const states = [...container.querySelectorAll('.pl-chip')].map((c) => c.getAttribute('data-state'));
    expect(states).toEqual(['done', 'done', 'pending', 'pending']);
  });
});
