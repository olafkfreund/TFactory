"""Captured output must keep the HEAD, not just the tail (#1195).

A 500-char tail-only clip preserved node stack frames and discarded the
"Cannot find module" line that names the cause -- the capture existed and
still could not answer why the lane failed.
"""

from __future__ import annotations

from agents.stability_runner import _clip


def test_short_text_is_untouched():
    assert _clip("boom", 100) == "boom"


def test_the_error_line_survives_a_long_stack_trace():
    err = "Error: Cannot find module 'typescript'"
    text = (
        err + "\n" + "\n".join(f"    at frame{i} (node:internal)" for i in range(400))
    )
    out = _clip(text, 400)

    assert err in out, "the cause was clipped away; only frames survived"


def test_the_end_also_survives():
    text = "HEAD" + ("x" * 5000) + "TAILMARK"
    out = _clip(text, 400)

    assert out.startswith("HEAD")
    assert out.endswith("TAILMARK")


def test_truncation_is_declared_not_silent():
    out = _clip("y" * 5000, 400)

    assert "truncated" in out
    assert len(out) < 600
