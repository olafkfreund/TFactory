/**
 * @vitest-environment jsdom
 *
 * Tests for <RegressionPanel> (RFC-0018 #489) — the portal regression surface
 * over GET /api/projects/{id}/regression. Fetching is injected via `fetchFn`,
 * so these assert render/empty/error/regression behaviour without a backend.
 */

import { describe, it, expect, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, waitFor } from '@testing-library/react';

import { RegressionPanel } from '../RegressionPanel';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

const EMPTY = {
  latest_run_id: null,
  baseline_run_id: null,
  has_regressions: null,
  runs: [],
  latest: null,
  latest_diff: null,
  latest_drift: null,
  coverage_trend: [],
  quarantined: [],
};

describe('<RegressionPanel>', () => {
  it('renders the empty state when a project has no runs', async () => {
    const fetchFn = vi.fn(() => jsonResponse(EMPTY)) as unknown as typeof fetch;
    render(<RegressionPanel projectId="demo" fetchFn={fetchFn} />);
    await waitFor(() => expect(screen.getByTestId('rp-empty')).toBeInTheDocument());
    // hit the right endpoint
    const url = (fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain('/api/projects/demo/regression');
  });

  it('shows the regression verdict, regressions, fixes, quarantine and history', async () => {
    const fetchFn = vi.fn(() =>
      jsonResponse({
        ...EMPTY,
        latest_run_id: 'r2',
        baseline_run_id: 'r1',
        has_regressions: true,
        runs: [
          { run_id: 'r1', ran_at: 't1', commit: null, totals: { total: 3, passed: 3, failed: 0, skipped: 0, quarantined: 0 }, coverage_pct: 80 },
          { run_id: 'r2', ran_at: 't2', commit: 'abc', totals: { total: 3, passed: 1, failed: 1, skipped: 0, quarantined: 1 }, coverage_pct: 78.4 },
        ],
        latest_diff: {
          run_id: 'r2',
          baseline_run_id: 'r1',
          has_regressions: true,
          counts: { regression: 1, fixed: 1 },
          entries: { broke: 'regression', repaired: 'fixed', steady: 'stable_pass' },
        },
        quarantined: [{ test_id: 'flaky', reason: 'chronic', since_run: 'r1', flip_rate: 0.5 }],
      }),
    ) as unknown as typeof fetch;

    render(<RegressionPanel projectId="demo" fetchFn={fetchFn} />);
    await waitFor(() => expect(screen.getByTestId('rp-verdict')).toHaveTextContent('regressions detected'));
    expect(screen.getByTestId('rp-regressions')).toHaveTextContent('broke');
    expect(screen.getByTestId('rp-fixed')).toHaveTextContent('repaired');
    expect(screen.getByTestId('rp-quarantined')).toHaveTextContent('flaky');
    expect(screen.getByTestId('rp-history')).toHaveTextContent('r2');
    // a stable test isn't surfaced as a regression
    expect(screen.getByTestId('rp-regressions')).not.toHaveTextContent('steady');
  });

  it('renders an error message when the request fails', async () => {
    const fetchFn = vi.fn(() =>
      jsonResponse({ detail: 'boom' }, false, 500),
    ) as unknown as typeof fetch;
    render(<RegressionPanel projectId="demo" fetchFn={fetchFn} />);
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });
});
