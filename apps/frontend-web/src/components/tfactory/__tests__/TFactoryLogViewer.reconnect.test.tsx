/**
 * @vitest-environment jsdom
 *
 * Reconnect-with-backoff tests for <TFactoryLogViewer>. A dropped stream
 * (abnormal/idle close) re-establishes after a backoff; terminal server codes
 * (4400/4404) do not retry.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { act, render } from '@testing-library/react';

import { TFactoryLogViewer } from '../TFactoryLogViewer';

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }
  triggerOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }
  triggerClose(code = 1006) {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent('close', { code }));
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

if (typeof globalThis.WebSocket === 'undefined') {
  // @ts-expect-error — jsdom shim
  globalThis.WebSocket = FakeWebSocket;
}

describe('TFactoryLogViewer reconnect', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('reconnects with backoff after an abnormal close', () => {
    const sockets: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const s = new FakeWebSocket(url);
      sockets.push(s);
      return s as unknown as WebSocket;
    };

    render(<TFactoryLogViewer specId="abc" wsBaseUrl="ws://t" wsFactory={wsFactory} />);
    expect(sockets).toHaveLength(1);

    act(() => sockets[0].triggerOpen());
    act(() => sockets[0].triggerClose(1006)); // abnormal — should retry

    // Backoff is 1s for the first attempt.
    act(() => { vi.advanceTimersByTime(1000); });
    expect(sockets).toHaveLength(2);
  });

  it('does NOT reconnect on a terminal 4404 (task not found)', () => {
    const sockets: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const s = new FakeWebSocket(url);
      sockets.push(s);
      return s as unknown as WebSocket;
    };

    render(<TFactoryLogViewer specId="ghost" wsBaseUrl="ws://t" wsFactory={wsFactory} />);
    act(() => sockets[0].triggerClose(4404));

    act(() => { vi.advanceTimersByTime(30_000); });
    expect(sockets).toHaveLength(1); // no retry
  });
});
