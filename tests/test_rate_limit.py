"""Tests for the fixed-window rate limiter (#242, epic #232)."""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.rate_limit import FixedWindowLimiter  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_allows_up_to_limit():
    lim = FixedWindowLimiter(3, 60, clock=_Clock())
    assert [lim.allow("k") for _ in range(3)] == [True, True, True]


def test_blocks_over_limit():
    lim = FixedWindowLimiter(2, 60, clock=_Clock())
    assert lim.allow("k") and lim.allow("k")
    assert lim.allow("k") is False


def test_window_resets():
    clk = _Clock()
    lim = FixedWindowLimiter(1, 10, clock=clk)
    assert lim.allow("k") is True
    assert lim.allow("k") is False
    clk.t = 11  # past the window
    assert lim.allow("k") is True


def test_keys_independent():
    lim = FixedWindowLimiter(1, 60, clock=_Clock())
    assert lim.allow("a") is True
    assert lim.allow("b") is True
    assert lim.allow("a") is False


def test_reset():
    lim = FixedWindowLimiter(1, 60, clock=_Clock())
    assert lim.allow("k") is True
    lim.reset("k")
    assert lim.allow("k") is True
    lim.reset()  # all
    assert lim.allow("k") is True
