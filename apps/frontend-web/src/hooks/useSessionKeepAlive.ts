/**
 * Session keep-alive — keep the portal's auth session warm and notice the
 * moment it dies, instead of letting a stale tab silently fail every poll.
 *
 * On the cluster the portal sits behind OIDC; locally it carries a Bearer
 * token. Either way, a long-idle tab can have its session expire server-side
 * while the page still looks logged in — the next action 401s out of nowhere.
 *
 * This hook pings a cheap authenticated endpoint (`/api/auth/me` by default)
 * on an interval and whenever the tab returns to the foreground. A 2xx keeps
 * the session warm; a 401/403 means the session is gone, so we call
 * `onAuthLost` once (the shell can prompt a re-login) rather than leaving the
 * user staring at stale data. Network blips are ignored — only a definitive
 * auth-failure status trips `onAuthLost`.
 */
import { useEffect, useRef } from 'react';
import { getAuthHeaders } from '../lib/auth';

const API_BASE = (import.meta.env?.VITE_API_BASE_URL as string | undefined) || '/api';

/** Default ping: GET /api/auth/me, returns the HTTP status (0 on network error). */
async function defaultPing(): Promise<number> {
  try {
    const res = await fetch(`${API_BASE}/auth/me`, {
      method: 'GET',
      credentials: 'include',
      headers: { ...getAuthHeaders() },
    });
    return res.status;
  } catch {
    return 0; // network/transport error — not an auth failure
  }
}

export interface SessionKeepAliveOptions {
  /** Master switch. Default true. */
  enabled?: boolean;
  /** Heartbeat interval in ms. Default 60_000 (1 min). `<= 0` disables. */
  intervalMs?: number;
  /** Test seam: replace the ping. Returns the HTTP status (0 = network error). */
  pingFn?: () => Promise<number>;
  /** Called once when the session is definitively gone (401/403). */
  onAuthLost?: () => void;
}

/**
 * Heartbeat the session. Pauses while the tab is hidden, pings immediately on
 * return to the foreground, and fires `onAuthLost` exactly once when a ping
 * comes back 401/403.
 */
export function useSessionKeepAlive(options: SessionKeepAliveOptions = {}): void {
  const {
    enabled = true,
    intervalMs = 60_000,
    pingFn = defaultPing,
    onAuthLost,
  } = options;

  const pingRef = useRef(pingFn);
  pingRef.current = pingFn;
  const onAuthLostRef = useRef(onAuthLost);
  onAuthLostRef.current = onAuthLost;
  // Latch so a dead session only notifies once until the hook re-mounts.
  const lostRef = useRef(false);

  useEffect(() => {
    if (!enabled || !intervalMs || intervalMs <= 0) return;

    const ping = async () => {
      const status = await pingRef.current();
      if (status === 401 || status === 403) {
        if (!lostRef.current) {
          lostRef.current = true;
          onAuthLostRef.current?.();
        }
      } else if (status >= 200 && status < 300) {
        lostRef.current = false; // session healthy again
      }
      // status === 0 (network blip) or 5xx: leave the latch untouched.
    };

    if (typeof document === 'undefined') {
      const id = setInterval(ping, intervalMs);
      return () => clearInterval(id);
    }

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(ping, intervalMs);
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
        void ping(); // catch-up heartbeat on return
        start();
      }
    };

    if (document.visibilityState !== 'hidden') start();
    document.addEventListener('visibilitychange', onForegroundChange);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onForegroundChange);
    };
  }, [enabled, intervalMs]);
}
