/**
 * @vitest-environment jsdom
 *
 * Tests for <VisualBaselines> (#109) — the portal viewer + accept flow over the
 * #160 visual-baseline API. Fetching is injected via `fetchFn`, so these assert
 * the list/render/empty/error/accept behaviour without a backend.
 */

import { describe, it, expect, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { VisualBaselines } from '../VisualBaselines';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

async function loadTarget(target: string) {
  fireEvent.change(screen.getByTestId('vb-target-input'), { target: { value: target } });
  fireEvent.click(screen.getByTestId('vb-load'));
}

describe('<VisualBaselines>', () => {
  it('lists baselines for a target and renders thumbnails', async () => {
    const fetchFn = vi.fn(() =>
      jsonResponse({
        target: 'storefront',
        baselines: [
          { snapshot: 'home.png', sizeBytes: 2048 },
          { snapshot: 'cart.png', sizeBytes: 4096 },
        ],
      }),
    ) as unknown as typeof fetch;

    render(<VisualBaselines specId="001-feat" fetchFn={fetchFn} />);
    await loadTarget('storefront');

    await waitFor(() => expect(screen.getByTestId('vb-grid')).toBeInTheDocument());
    expect(screen.getByTestId('vb-item-home.png')).toBeInTheDocument();
    expect(screen.getByTestId('vb-item-cart.png')).toBeInTheDocument();
    expect(screen.getByTestId('vb-count')).toHaveTextContent('2');

    // The list call hit the right endpoint with the target query.
    const url = (fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain('/001-feat/visual-baselines?target=storefront');

    // Thumbnail src points at the serve endpoint.
    const img = screen.getByTestId('vb-img-home.png') as HTMLImageElement;
    expect(img.src).toContain('/visual-baselines/storefront/home.png');
  });

  it('shows an empty state when a target has no baselines', async () => {
    const fetchFn = vi.fn(() => jsonResponse({ target: 'empty', baselines: [] })) as unknown as typeof fetch;
    render(<VisualBaselines specId="001-feat" fetchFn={fetchFn} />);
    await loadTarget('empty');
    await waitFor(() => expect(screen.getByTestId('vb-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('vb-grid')).not.toBeInTheDocument();
  });

  it('surfaces an API error', async () => {
    const fetchFn = vi.fn(() => jsonResponse({ detail: 'unsafe target' }, false, 400)) as unknown as typeof fetch;
    render(<VisualBaselines specId="001-feat" fetchFn={fetchFn} />);
    await loadTarget('../escape');
    await waitFor(() => expect(screen.getByTestId('vb-error')).toHaveTextContent('unsafe target'));
  });

  it('promotes a captured screenshot to the baseline (accept)', async () => {
    const calls: { url: string; method?: string; body?: string }[] = [];
    const fetchFn = vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: init?.body as string | undefined });
      if (url.includes('/accept')) return jsonResponse({ accepted: true, target: 't', snapshot: 'home.png', path: 'x' });
      return jsonResponse({ target: 't', baselines: [] });
    }) as unknown as typeof fetch;

    render(
      <VisualBaselines
        specId="001-feat"
        fetchFn={fetchFn}
        captures={[{ name: 'home.png', path: 'findings/runs/t1/screenshots/home.png' }]}
      />,
    );
    await loadTarget('t');
    await waitFor(() => expect(screen.getByTestId('vb-accept')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('vb-snapshot'), { target: { value: 'home.png' } });
    fireEvent.click(screen.getByTestId('vb-accept-btn'));

    await waitFor(() => expect(screen.getByTestId('vb-accept-msg')).toHaveTextContent('Promoted'));
    const accept = calls.find((c) => c.url.includes('/accept'))!;
    expect(accept.method).toBe('POST');
    expect(accept.url).toContain('/visual-baselines/t/home.png/accept');
    expect(JSON.parse(accept.body!)).toEqual({ source: 'findings/runs/t1/screenshots/home.png' });
  });
});
