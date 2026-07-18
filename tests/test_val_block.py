"""Tests for the RFC-0006 VAL block mapper (#74)."""

from __future__ import annotations

from agents.val_block import build_verification_block


def _v(lane: str, verdict: str, n: int = 1) -> list[dict]:
    return [
        {"test_id": f"{lane}-{i}", "lane": lane, "verdict": verdict} for i in range(n)
    ]


def test_unit_and_api_pass_reaches_val2_with_val3_gap() -> None:
    block = build_verification_block(_v("unit", "accept", 3) + _v("api", "accept", 2))
    assert block["achieved_level"] == "VAL-2"
    # VAL-3 is honestly surfaced as a gap, never silently "done"
    assert "VAL-3 not_run" in block["claim"]
    assert not block["_gate"]["downgraded"]


def test_unit_only_reaches_val1_with_val2_gap() -> None:
    block = build_verification_block(_v("unit", "accept", 2))
    assert block["achieved_level"] == "VAL-1"
    assert "VAL-2 not_run" in block["claim"]


def test_a_unit_failure_caps_the_ceiling_to_val0() -> None:
    # a rejected unit test (VAL-1 failed) caps achieved below it even if api passed
    block = build_verification_block(_v("unit", "reject", 1) + _v("api", "accept", 2))
    assert block["achieved_level"] == "VAL-0"
    val1 = next(lv for lv in block["levels"] if lv["level"] == "VAL-1")
    assert val1["status"] == "failed"


def test_api_not_run_ac_keeps_val2_not_run_not_failed() -> None:
    # #703 follow-up: the app-under-test never booted, so its endpoint AC is
    # not_run (infra), not a reject. VAL-2 must stay not_run (gate downgrades to
    # VAL-1) WITHOUT recording a false AC failure that would cap the ceiling.
    block = build_verification_block(_v("unit", "accept", 2) + _v("api", "not_run", 1))
    assert block["achieved_level"] == "VAL-1"
    val2 = next(lv for lv in block["levels"] if lv["level"] == "VAL-2")
    assert val2["status"] == "not_run"
    assert "did not execute against a running" in val2["reason"]
    assert "VAL-2 not_run" in block["claim"]
    # not_run is a gap, not a failure — nothing was falsely rejected.
    assert not any("failed" in lv.get("status", "") for lv in block["levels"][:3])


def test_api_not_run_ignored_when_other_api_acs_pass() -> None:
    # A not_run AC alongside real passes doesn't drag VAL-2 down: it's excluded,
    # the remaining passes stand.
    block = build_verification_block(
        _v("unit", "accept", 1) + _v("api", "accept", 1) + _v("api", "not_run", 1)
    )
    assert block["achieved_level"] == "VAL-2"


def test_a_failed_level_carries_a_reason_and_gate_flags_no_missing_reason() -> None:
    # A ran-but-failed level must carry an explanation, else the gate stamps
    # missing_reason:<level> as its own violation (regression guard).
    block = build_verification_block(_v("unit", "reject", 1) + _v("unit", "flag", 3))
    val1 = next(lv for lv in block["levels"] if lv["level"] == "VAL-1")
    assert val1["status"] == "failed"
    assert val1.get("reason"), "failed VAL-1 must carry a reason"
    assert "1/4" in val1["reason"]
    assert not any(v.startswith("missing_reason") for v in block["_gate"]["violations"])


def test_no_verdicts_is_not_verified() -> None:
    block = build_verification_block([])
    assert block["achieved_level"] == "VAL-0"
    assert "NOT VERIFIED" in block["claim"]


def test_browser_lane_counts_as_val2() -> None:
    block = build_verification_block(_v("unit", "accept") + _v("browser", "accept"))
    assert block["achieved_level"] == "VAL-2"


def test_flag_verdict_counts_as_pass() -> None:
    block = build_verification_block(_v("unit", "flag", 2))
    assert block["achieved_level"] == "VAL-1"


def test_val3_always_carries_a_reason_and_never_overclaims() -> None:
    block = build_verification_block(_v("unit", "accept") + _v("api", "accept"))
    val3 = next(lv for lv in block["levels"] if lv["level"] == "VAL-3")
    assert val3["status"] == "not_run" and "#75" in val3["reason"]
    # the gate never lets achieved_level reach VAL-3 without a passed VAL-3
    assert block["achieved_level"] != "VAL-3"
