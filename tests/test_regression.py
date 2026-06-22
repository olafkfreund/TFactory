"""Tests for the regression store + diff/classification — RFC-0018 #483."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.flaky_history import FlakyClass  # noqa: E402
from agents.regression import (  # noqa: E402
    RegressionClass,
    RegressionRun,
    TestOutcome,
    TestStatus,
    classify,
    diff_runs,
    list_runs,
    load_baseline,
    load_latest,
    load_run,
    regression_dir,
    render_json,
    render_markdown,
    save_run,
    set_baseline,
)


# ── helpers ─────────────────────────────────────────────────────────────
def _outcome(test_id: str, status: TestStatus) -> TestOutcome:
    return TestOutcome(
        test_id=test_id, lane="unit", framework="pytest", status=status
    )


def _run(run_id: str, outcomes: dict[str, TestStatus], **kw) -> RegressionRun:
    return RegressionRun(
        run_id=run_id,
        project_id=kw.pop("project_id", "demo"),
        ran_at=kw.pop("ran_at", "2026-06-22T12:00:00Z"),
        results=tuple(_outcome(t, s) for t, s in outcomes.items()),
        **kw,
    )


# ── TestStatus semantics ─────────────────────────────────────────────────
def test_status_fail_semantics():
    assert TestStatus.FAILED.is_fail and TestStatus.ERROR.is_fail
    assert not TestStatus.SKIPPED.is_fail
    assert not TestStatus.QUARANTINED.is_fail
    assert TestStatus.PASSED.is_pass


# ── totals ─────────────────────────────────────────────────────────────
def test_run_totals_and_failed_flag():
    run = _run(
        "r1",
        {
            "a": TestStatus.PASSED,
            "b": TestStatus.FAILED,
            "c": TestStatus.ERROR,
            "d": TestStatus.SKIPPED,
            "e": TestStatus.QUARANTINED,
        },
    )
    t = run.totals
    assert t == {"total": 5, "passed": 1, "failed": 2, "skipped": 1, "quarantined": 1}
    assert run.failed is True


def test_run_not_failed_when_only_skip_quarantine():
    run = _run("r1", {"a": TestStatus.PASSED, "b": TestStatus.QUARANTINED})
    assert run.failed is False


# ── model round-trip ─────────────────────────────────────────────────────
def test_run_dict_roundtrip():
    run = _run(
        "r1",
        {"a": TestStatus.PASSED, "b": TestStatus.FAILED},
        commit="abc123",
        target_url="https://staging",
        coverage_pct=81.5,
    )
    again = RegressionRun.from_dict(run.to_dict())
    assert again == run
    assert run.to_dict()["schema_version"] == "1.0"


# ── classification precedence ─────────────────────────────────────────────
def test_classify_regression_and_fixed():
    assert (
        classify(baseline_status=TestStatus.PASSED, current_status=TestStatus.FAILED)
        is RegressionClass.REGRESSION
    )
    assert (
        classify(baseline_status=TestStatus.FAILED, current_status=TestStatus.PASSED)
        is RegressionClass.FIXED
    )


def test_classify_still_failing_and_stable():
    assert (
        classify(baseline_status=TestStatus.FAILED, current_status=TestStatus.ERROR)
        is RegressionClass.STILL_FAILING
    )
    assert (
        classify(baseline_status=TestStatus.PASSED, current_status=TestStatus.PASSED)
        is RegressionClass.STABLE_PASS
    )


def test_classify_new_dropped_quarantine_flaky_precedence():
    # dropped wins (current absent) even over a quarantined-looking baseline
    assert (
        classify(baseline_status=TestStatus.PASSED, current_status=None)
        is RegressionClass.DROPPED
    )
    # quarantined current beats new/flaky
    assert (
        classify(baseline_status=None, current_status=TestStatus.QUARANTINED)
        is RegressionClass.QUARANTINED
    )
    # new (no baseline) beats flaky
    assert (
        classify(
            baseline_status=None, current_status=TestStatus.PASSED, is_flaky=True
        )
        is RegressionClass.NEW
    )
    # flaky beats a pass->fail transition (don't alarm on a known flake)
    assert (
        classify(
            baseline_status=TestStatus.PASSED,
            current_status=TestStatus.FAILED,
            is_flaky=True,
        )
        is RegressionClass.FLAKY
    )


# ── diff over two runs ─────────────────────────────────────────────────
def test_diff_covers_every_class():
    baseline = _run(
        "base",
        {
            "reg": TestStatus.PASSED,  # -> regression
            "fix": TestStatus.FAILED,  # -> fixed
            "stillfail": TestStatus.FAILED,  # -> still_failing
            "stable": TestStatus.PASSED,  # -> stable_pass
            "flake": TestStatus.PASSED,  # -> flaky (via lookup)
            "gone": TestStatus.PASSED,  # -> dropped
        },
    )
    current = _run(
        "cur",
        {
            "reg": TestStatus.FAILED,
            "fix": TestStatus.PASSED,
            "stillfail": TestStatus.FAILED,
            "stable": TestStatus.PASSED,
            "flake": TestStatus.FAILED,
            "fresh": TestStatus.PASSED,  # -> new
        },
        baseline_run_id="base",
    )

    def flaky_lookup(tid: str) -> FlakyClass:
        return FlakyClass.FLAKY if tid == "flake" else FlakyClass.STABLE

    diff = diff_runs(current, baseline, flaky_lookup=flaky_lookup)
    got = dict(diff.entries)
    assert got["reg"] is RegressionClass.REGRESSION
    assert got["fix"] is RegressionClass.FIXED
    assert got["stillfail"] is RegressionClass.STILL_FAILING
    assert got["stable"] is RegressionClass.STABLE_PASS
    assert got["flake"] is RegressionClass.FLAKY
    assert got["gone"] is RegressionClass.DROPPED
    assert got["fresh"] is RegressionClass.NEW
    assert diff.has_regressions is True
    assert diff.regressions == ("reg",)


def test_diff_no_baseline_all_new():
    current = _run("cur", {"a": TestStatus.PASSED, "b": TestStatus.FAILED})
    diff = diff_runs(current, None)
    assert {tid: c for tid, c in diff.entries} == {
        "a": RegressionClass.NEW,
        "b": RegressionClass.NEW,
    }
    assert diff.has_regressions is False


def test_diff_entries_sorted_and_roundtrip():
    current = _run("cur", {"z": TestStatus.PASSED, "a": TestStatus.PASSED})
    diff = diff_runs(current, None)
    assert [tid for tid, _ in diff.entries] == ["a", "z"]  # deterministic order
    d = diff.to_dict()
    assert d["counts"]["new"] == 2
    assert d["has_regressions"] is False


# ── store ────────────────────────────────────────────────────────────────
def test_store_save_load_and_pointers(tmp_path):
    reg = regression_dir(tmp_path, "demo")
    run1 = _run("r1", {"a": TestStatus.PASSED})
    save_run(reg, run1)
    # first run becomes both latest and baseline
    assert load_run(reg, "r1") == run1
    assert load_latest(reg) == run1
    assert load_baseline(reg) == run1

    run2 = _run("r2", {"a": TestStatus.FAILED}, baseline_run_id="r1")
    save_run(reg, run2)
    assert load_latest(reg) == run2  # latest advanced
    assert load_baseline(reg) == run1  # baseline stayed pinned
    assert list_runs(reg) == ["r1", "r2"]


def test_store_set_baseline(tmp_path):
    reg = regression_dir(tmp_path, "demo")
    save_run(reg, _run("r1", {"a": TestStatus.PASSED}))
    save_run(reg, _run("r2", {"a": TestStatus.PASSED}))
    set_baseline(reg, "r2")
    assert load_baseline(reg).run_id == "r2"
    with pytest.raises(FileNotFoundError):
        set_baseline(reg, "nope")


def test_store_empty_dir_safe(tmp_path):
    reg = regression_dir(tmp_path, "demo")
    assert load_latest(reg) is None
    assert load_baseline(reg) is None
    assert load_run(reg, "missing") is None
    assert list_runs(reg) == []


# ── report rendering ─────────────────────────────────────────────────────
def test_render_markdown_deterministic_and_flags_regression():
    baseline = _run("base", {"reg": TestStatus.PASSED})
    current = _run("cur", {"reg": TestStatus.FAILED}, baseline_run_id="base")
    diff = diff_runs(current, baseline)
    md = render_markdown(diff, current)
    assert "REGRESSIONS DETECTED" in md
    assert "`reg`" in md
    # deterministic: same inputs -> identical output
    assert md == render_markdown(diff_runs(current, baseline), current)


def test_render_markdown_clean_run():
    current = _run("cur", {"a": TestStatus.PASSED}, coverage_pct=90.0)
    md = render_markdown(diff_runs(current, None), current)
    assert "no regressions" in md
    assert "90.0%" in md


def test_render_json_shape():
    current = _run("cur", {"a": TestStatus.PASSED})
    out = render_json(diff_runs(current, None), current)
    assert out["run"]["run_id"] == "cur"
    assert out["diff"]["counts"]["new"] == 1
