/**
 * @vitest-environment jsdom
 *
 * Tests for useSessionKeepAlive — the portal's session heartbeat. A 401/403
 * ping fires onAuthLost exactly once; healthy pings and network blips do not.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useSessionKeepAlive } from '../useSessionKeepAlive';

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  Object.defineProperty(document, 'hidden', { value: state === 'hidden', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('useSessionKeepAlive', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('fires onAuthLost once when a ping returns 401', async () => {
    const pingFn = vi.fn().mockResolvedValue(401);
    const onAuthLost = vi.fn();
    renderHook(() => useSessionKeepAlive({ intervalMs: 1000, pingFn, onAuthLost }));

    await vi.advanceTimersByTimeAsync(1000);
    expect(onAuthLost).toHaveBeenCalledTimes(1);

    // A second failing ping must not re-fire (latched).
    await vi.advanceTimersByTimeAsync(1000);
    expect(onAuthLost).toHaveBeenCalledTimes(1);
  });

  it('does not fire on healthy pings', async () => {
    const pingFn = vi.fn().mockResolvedValue(200);
    const onAuthLost = vi.fn();
    renderHook(() => useSessionKeepAlive({ intervalMs: 1000, pingFn, onAuthLost }));
    await vi.advanceTimersByTimeAsync(3000);
    expect(pingFn).toHaveBeenCalled();
    expect(onAuthLost).not.toHaveBeenCalled();
  });

  it('ignores network blips (status 0)', async () => {
    const pingFn = vi.fn().mockResolvedValue(0);
    const onAuthLost = vi.fn();
    renderHook(() => useSessionKeepAlive({ intervalMs: 1000, pingFn, onAuthLost }));
    await vi.advanceTimersByTimeAsync(3000);
    expect(onAuthLost).not.toHaveBeenCalled();
  });

  it('pings immediately when the tab returns to the foreground', async () => {
    const pingFn = vi.fn().mockResolvedValue(200);
    renderHook(() => useSessionKeepAlive({ intervalMs: 100_000, pingFn }));

    setVisibility('hidden');
    setVisibility('visible');
    await vi.advanceTimersByTimeAsync(0);
    expect(pingFn).toHaveBeenCalled();
  });

  it('is disabled when enabled=false', async () => {
    const pingFn = vi.fn().mockResolvedValue(200);
    renderHook(() => useSessionKeepAlive({ enabled: false, intervalMs: 1000, pingFn }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(pingFn).not.toHaveBeenCalled();
  });
});
