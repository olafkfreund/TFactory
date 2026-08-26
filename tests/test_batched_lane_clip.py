"""The batched nix lane must clip like everything else (#1195).

`check_stability` was fixed to keep both ends of captured output, but the
nix batched lane -- which is what the jest and browser lanes actually use --
builds StabilityRun itself with a hardcoded `tail[-500:]`. So the fix landed
in a function this lane never calls, and spec 186 still captured exactly 500
chars of stack frames with the error line missing.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents.evaluator import _clip

_EVALUATOR = Path(__file__).resolve().parents[1] / "apps/backend/agents/evaluator.py"


def test_no_hardcoded_tail_truncation_remains():
    src = _EVALUATOR.read_text()
    hits = re.findall(r"_tail=\w+\[-\d+:\]", src)

    assert not hits, f"hardcoded truncation bypasses _clip: {hits}"


def test_the_lane_uses_the_shared_clipper():
    src = _EVALUATOR.read_text()

    assert "_clip(tail," in src, "batched lane no longer routes through _clip"


def test_clip_keeps_the_error_line():
    """The behaviour the lane actually needs, exercised end to end."""
    err = "Error: Cannot find module 'typescript'"
    text = err + "\n" + "\n".join(f"    at f{i} (node:internal)" for i in range(300))

    assert err in _clip(text, 500)
