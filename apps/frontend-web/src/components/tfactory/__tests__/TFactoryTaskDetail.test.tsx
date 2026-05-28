/**
 * @vitest-environment jsdom
 *
 * Tests for <TFactoryTaskDetail> — Task 10 (#11) commit 3.
 *
 * Uses a URL-aware fetchFn mock so a single test can stub multiple
 * endpoints (detail + verdicts + report) and verify lazy-load
 * behaviour.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { TFactoryTaskDetail } from '../TFactoryTaskDetail';

beforeEach(() => {
  localStorage.setItem('tfactory-token', 'test-token');
});

// ── URL-aware fetch helper ────────────────────────────────────────────

interface UrlResponse {
  ok?: boolean;
  status?: number;
  statusText?: string;
  jsonBody?: unknown;
  textBody?: string;
}

function makeUrlAwareFetch(routes: Record<string, UrlResponse>): typeof fetch {
  return vi.fn().mockImplementation(async (input: RequestInfo) => {
    const url = typeof input === 'string' ? input : input.url;
    // Find first registered route that matches as substring
    let match: UrlResponse | undefined;
    for (const key of Object.keys(routes)) {
      if (url.endsWith(key) || url === key) {
        match = routes[key];
        break;
      }
    }
    const opts: UrlResponse = match ?? { ok: false, status: 404, statusText: 'Not Found' };
    return {
      ok: opts.ok ?? true,
      status: opts.status ?? 200,
      statusText: opts.statusText ?? 'OK',
      json: async () => opts.jsonBody,
      text: async () => opts.textBody ?? '',
    };
  }) as unknown as typeof fetch;
}

const sampleDetail = (overrides: Partial<{
  status: string;
  verdictsExists: boolean;
  reportExists: boolean;
}> = {}) => ({
  task_id: 'spec-x',
  project_id: 'demo',
  spec_id: 'spec-x',
  status_json: { status: overrides.status ?? 'triaged' },
  artefacts: {
    test_plan: { path: 'test_plan.json', exists: true },
    verdicts: { path: 'findings/verdicts.json', exists: overrides.verdictsExists ?? true },
    triage_report_json: { path: 'findings/triage_report.json', exists: true },
    triage_report_md: { path: 'findings/triage_report.md', exists: overrides.reportExists ?? true },
    pr_comment_body: { path: 'findings/pr_comment_body.md', exists: false },
  },
});

// ── Initial fetch / loading / error ───────────────────────────────────

describe('<TFactoryTaskDetail> initial fetch', () => {
  it('shows loading state before detail resolves', () => {
    const fetchFn = vi.fn().mockReturnValue(
      new Promise(() => { /* never resolves */ }),
    ) as unknown as typeof fetch;
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/Loading task/i)).toBeInTheDocument();
  });

  it('renders header + tabs after detail resolves', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);

    await waitFor(() =>
      expect(screen.getByTestId('tfactory-task-detail')).toBeInTheDocument(),
    );
    expect(screen.getByRole('heading', { name: 'spec-x' })).toBeInTheDocument();
    expect(screen.getByTestId('tab-status')).toBeInTheDocument();
    expect(screen.getByTestId('tab-lanes')).toBeInTheDocument();
    expect(screen.getByTestId('tab-verdicts')).toBeInTheDocument();
    expect(screen.getByTestId('tab-report')).toBeInTheDocument();
    expect(screen.getByTestId('tab-logs')).toBeInTheDocument();
  });

  it('shows alert when detail fetch returns 500', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': {
        ok: false, status: 500, statusText: 'Internal Server Error',
        jsonBody: { detail: 'database down' },
      },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText(/database down/)).toBeInTheDocument();
  });
});

// ── Default tab: Status ──────────────────────────────────────────────

describe('<TFactoryTaskDetail> status tab', () => {
  it('is the default active tab and shows status_json', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ status: 'triaged' }) },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() =>
      expect(screen.getByTestId('panel-status')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('tab-status')).toHaveAttribute('data-active', 'true');
    // JSON pretty-printed in the panel
    expect(screen.getByTestId('panel-status').textContent).toMatch(/triaged/);
  });
});

// ── Lanes tab ────────────────────────────────────────────────────────

describe('<TFactoryTaskDetail> lanes tab', () => {
  it('switches to lanes tab and renders the grid with unit status', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ status: 'triaged' }) },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-lanes'));

    fireEvent.click(screen.getByTestId('tab-lanes'));
    expect(screen.getByTestId('lane-status-grid')).toBeInTheDocument();
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', 'success',
    );
    expect(screen.getByTestId('lane-card-browser')).toHaveAttribute(
      'data-lane-state', 'placeholder',
    );
  });
});

// ── Verdicts tab (lazy load) ─────────────────────────────────────────

describe('<TFactoryTaskDetail> verdicts tab', () => {
  it('fetches verdicts lazily on tab open', async () => {
    const verdictsBody = {
      evaluator_version: 'task7-commit5',
      mode: 'initial',
      generated_at: '2026-05-28T00:00:00+00:00',
      verdicts: [
        {
          test_id: 't0', test_file: 'tests/test_0.py',
          verdict: 'accept', reasons: ['ok'],
          signals_summary: {
            coverage_delta_pct: 5.5, stability: 'stable', mutation: 'killed',
          },
        },
        {
          test_id: 't1', test_file: 'tests/test_1.py',
          verdict: 'reject', reasons: ['bad'],
          signals_summary: {
            coverage_delta_pct: 0, stability: 'stable', mutation: 'survived',
          },
        },
      ],
    };
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/verdicts.json': { jsonBody: verdictsBody },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    // Verdicts NOT requested yet
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);

    fireEvent.click(screen.getByTestId('tab-verdicts'));

    await waitFor(() => screen.getByTestId('verdict-table'));
    expect(screen.getByTestId('verdict-row-t0')).toBeInTheDocument();
    expect(screen.getByTestId('verdict-row-t1')).toBeInTheDocument();
    // Now 2 fetches total
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2);
  });

  it('disables Verdicts tab when artefact is absent', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: false }) },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));
    expect(screen.getByTestId('tab-verdicts')).toBeDisabled();
  });

  it('shows friendly 404 message when verdicts endpoint returns 404', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/verdicts.json': {
        ok: false, status: 404, statusText: 'Not Found',
        jsonBody: { detail: 'artefact not found' },
      },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));
    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toBeInTheDocument(),
    );
    expect(screen.getByText(/hasn't reached the Evaluator/i)).toBeInTheDocument();
  });
});

// ── Report tab (lazy load) ───────────────────────────────────────────

describe('<TFactoryTaskDetail> report tab', () => {
  it('lazy-fetches the markdown and renders it on tab open', async () => {
    const md = '# Triage Report\n\nLooks good.\n';
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/triage-report.md': { textBody: md },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));

    fireEvent.click(screen.getByTestId('tab-report'));
    await waitFor(() => screen.getByTestId('report-md-content'));
    expect(screen.getByTestId('report-md-content').textContent).toBe(md);
  });

  it('disables Report tab when triage_report_md artefact is absent', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ reportExists: false }) },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));
    expect(screen.getByTestId('tab-report')).toBeDisabled();
  });

  it('shows friendly 404 message when report endpoint returns 404', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/triage-report.md': {
        ok: false, status: 404, statusText: 'Not Found',
        jsonBody: { detail: 'artefact not found' },
      },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));
    fireEvent.click(screen.getByTestId('tab-report'));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toBeInTheDocument(),
    );
    expect(screen.getByText(/hasn't reached the Triager/i)).toBeInTheDocument();
  });

  it('does not refetch report on subsequent tab opens', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/triage-report.md': { textBody: '# Report\n' },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));

    // First open
    fireEvent.click(screen.getByTestId('tab-report'));
    await waitFor(() => screen.getByTestId('report-md-content'));
    const callsAfterFirst = (fetchFn as ReturnType<typeof vi.fn>).mock.calls.length;

    // Navigate away + back
    fireEvent.click(screen.getByTestId('tab-status'));
    fireEvent.click(screen.getByTestId('tab-report'));
    // No additional fetch — cached
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(callsAfterFirst);
  });
});

// ── Tab active state semantics ───────────────────────────────────────

describe('<TFactoryTaskDetail> tab active state', () => {
  it('updates aria-selected as tabs change', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
    });
    render(<TFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-lanes'));

    expect(screen.getByTestId('tab-status')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-lanes')).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(screen.getByTestId('tab-lanes'));
    expect(screen.getByTestId('tab-lanes')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-status')).toHaveAttribute('aria-selected', 'false');
  });
});
