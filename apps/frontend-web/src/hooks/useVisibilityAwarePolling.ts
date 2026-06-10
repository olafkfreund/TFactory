/**
 * Visibility-aware polling — keep the portal fresh without burning requests
 * on a backgrounded tab.
 *
 * The TFactory task list + detail views auto-refresh on an interval so live
 * status changes (a lane flipping to running, a watchdog `stalled`) surface
 * without a manual reload. A naive `setInterval` keeps firing while the tab is
 * hidden (wasted load) and, worse, shows stale data the instant the user comes
 * back. These helpers fix both:
 *
 *   • pause the interval while `document.visibilityState === 'hidden'`;
 *   • fire the callback once immediately when the tab returns to the
 *     foreground, so the user sees fresh data the moment they look.
 *
 * Both are SSR-safe (no-op when `document` is undefined) and keep the latest
 * callback in a ref so the interval isn't torn down on every render.
 */
import { useEffect, useRef, useState } from 'react';

/** True when the document is in the foreground. SSR-safe (defaults to true). */
export function usePageVisibility(): boolean {
  const read = () =>
    typeof document === 'undefined' ? true : document.visibilityState !== 'hidden';
  const [visible, setVisible] = useState(read);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const onChange = () => setVisible(read());
    document.addEventListener('visibilitychange', onChange);
    window.addEventListener('focus', onChange);
    return () => {
      document.removeEventListener('visibilitychange', onChange);
      window.removeEventListener('focus', onChange);
    };
  }, []);

  return visible;
}

/**
 * Invoke `callback` every `intervalMs` ms while the tab is in the foreground,
 * and once immediately whenever the tab returns to the foreground.
 *
 * `intervalMs <= 0` disables polling entirely (the caller's initial-load
 * effect still runs). The callback is read from a ref, so changing it between
 * renders does not restart the timer.
 */
export function useVisibilityAwarePolling(
  callback: () => void,
  intervalMs: number,
): void {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    if (!intervalMs || intervalMs <= 0) return;

    // Non-browser env (jsdom without visibility API / SSR): plain interval.
    if (typeof document === 'undefined') {
      const id = setInterval(() => cbRef.current(), intervalMs);
      return () => clearInterval(id);
    }

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(() => cbRef.current(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onForegroundChange = () => {
      if (document.visibilityState === 'hidden') {
        stop();
      } else {
        cbRef.current(); // immediate catch-up refetch on return
        start();
      }
    };

    if (document.visibilityState !== 'hidden') start();
    document.addEventListener('visibilitychange', onForegroundChange);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onForegroundChange);
    };
  }, [intervalMs]);
}
