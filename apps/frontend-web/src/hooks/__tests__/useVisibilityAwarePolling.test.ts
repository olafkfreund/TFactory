/**
 * @vitest-environment jsdom
 *
 * Tests for useVisibilityAwarePolling + usePageVisibility — the portal's
 * background auto-refresh, paused while the tab is hidden and caught up the
 * instant it returns to the foreground.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useVisibilityAwarePolling, usePageVisibility } from '../useVisibilityAwarePolling';

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  Object.defineProperty(document, 'hidden', { value: state === 'hidden', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('useVisibilityAwarePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('fires the callback on the interval while visible', () => {
    const cb = vi.fn();
    renderHook(() => useVisibilityAwarePolling(cb, 1000));
    expect(cb).not.toHaveBeenCalled();
    vi.advanceTimersByTime(3000);
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it('pauses while hidden and resumes (with an immediate catch-up) on return', () => {
    const cb = vi.fn();
    renderHook(() => useVisibilityAwarePolling(cb, 1000));

    vi.advanceTimersByTime(1000);
    expect(cb).toHaveBeenCalledTimes(1);

    setVisibility('hidden');
    vi.advanceTimersByTime(5000);
    expect(cb).toHaveBeenCalledTimes(1); // no ticks while hidden

    setVisibility('visible');
    expect(cb).toHaveBeenCalledTimes(2); // immediate catch-up on return
    vi.advanceTimersByTime(1000);
    expect(cb).toHaveBeenCalledTimes(3); // interval resumed
  });

  it('does nothing when intervalMs <= 0', () => {
    const cb = vi.fn();
    renderHook(() => useVisibilityAwarePolling(cb, 0));
    vi.advanceTimersByTime(10000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('clears the interval on unmount', () => {
    const cb = vi.fn();
    const { unmount } = renderHook(() => useVisibilityAwarePolling(cb, 1000));
    vi.advanceTimersByTime(1000);
    expect(cb).toHaveBeenCalledTimes(1);
    unmount();
    vi.advanceTimersByTime(5000);
    expect(cb).toHaveBeenCalledTimes(1);
  });
});

describe('usePageVisibility', () => {
  beforeEach(() => setVisibility('visible'));

  it('tracks foreground/background transitions', () => {
    const { result } = renderHook(() => usePageVisibility());
    expect(result.current).toBe(true);
    act(() => setVisibility('hidden'));
    expect(result.current).toBe(false);
    act(() => setVisibility('visible'));
    expect(result.current).toBe(true);
  });
});
