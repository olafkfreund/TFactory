"""Tiny in-memory fixed-window rate limiter (#242, epic #232).

Used to throttle the public/secret-gated webhook endpoints (e.g. the inbound
handback webhook) so a leaked secret or a misbehaving caller can't drive an
unbounded number of pipeline re-fires. In-process + best-effort — good enough
for a single-node portal; a multi-node deployment would swap in a shared store.

Pure and clock-injectable so it's unit-testable without sleeping.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable


class FixedWindowLimiter:
    """Allow at most ``max_calls`` per ``window_seconds`` per key.

    ``clock`` returns a monotonic seconds float (injectable for tests).
    """

    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        """Record a call for ``key``; return False when over the limit."""
        now = self._clock()
        cutoff = now - self.window
        hits = [t for t in self._hits[key] if t > cutoff]
        if len(hits) >= self.max_calls:
            self._hits[key] = hits  # keep the pruned window; reject this call
            return False
        hits.append(now)
        self._hits[key] = hits
        return True

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)
