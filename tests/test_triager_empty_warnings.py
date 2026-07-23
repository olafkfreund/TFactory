#!/usr/bin/env python3
"""Loud diagnostics for a hollow verdict — `_triaged_empty_warnings` (#729).

A triaged_empty that came from rejecting 100% of the generated tests (or an
all-unverified AC fidelity) must surface a warning rather than read as a clean
"nothing to do".
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.triager import _triaged_empty_warnings  # noqa: E402


def test_hundred_percent_rejection_warns() -> None:
    # #729's exact signature: 14 generated, 14 rejected, none committed/flagged.
    warns = _triaged_empty_warnings(
        "triaged_empty",
        committed_count=0,
        flagged_count=0,
        rejected_count=14,
        ac_summary={"verified": 0, "total": 6, "all_verified": False},
    )
    assert any("100% rejection" in w and "14" in w for w in warns)
    assert any("acceptance criteria verified" in w for w in warns)


def test_partial_success_is_silent() -> None:
    # A real verdict with committed tests is not a hollow run — no warning.
    warns = _triaged_empty_warnings(
        "triaged",
        committed_count=5,
        flagged_count=1,
        rejected_count=9,
        ac_summary={"verified": 6, "total": 6, "all_verified": True},
    )
    assert warns == []


def test_empty_with_no_candidates_is_not_flagged_as_systematic() -> None:
    # triaged_empty with nothing rejected (genuinely nothing generated) is not
    # the systematic-failure signature — don't cry wolf.
    warns = _triaged_empty_warnings(
        "triaged_empty",
        committed_count=0,
        flagged_count=0,
        rejected_count=0,
        ac_summary={"verified": 3, "total": 3, "all_verified": True},
    )
    assert warns == []


def test_partial_ac_fidelity_is_not_loud() -> None:
    # Some ACs verified is normal; only a fully-unverified fidelity is alarming.
    warns = _triaged_empty_warnings(
        "triaged",
        committed_count=2,
        flagged_count=0,
        rejected_count=1,
        ac_summary={"verified": 2, "total": 6, "all_verified": False},
    )
    assert warns == []
