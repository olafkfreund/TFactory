"""Sanity check for the real captured run-026 fixtures (#1344 step 1).

These two files are verbatim ``stdout_tail`` evidence pulled from the
cluster's ``lane_runs.json`` for run 026-myfriends-remediation-verify:
``target_url_keyerror.txt`` (test-ownership check that never reached the
subject — missing env var) and ``worktree_absent.txt`` (the documented,
not-covered ``/tmp/.worktree`` case). A fixture that silently failed to
load would make every later classification test in this plan vacuous.
"""

from __future__ import annotations

from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "lane_runs"


def test_target_url_keyerror_fixture_is_not_empty() -> None:
    text = (_FIXTURES / "target_url_keyerror.txt").read_text(encoding="utf-8")
    assert text.strip() != ""


def test_target_url_keyerror_fixture_contains_the_marker() -> None:
    text = (_FIXTURES / "target_url_keyerror.txt").read_text(encoding="utf-8")
    assert "KeyError: 'TFACTORY_TARGET_URL'" in text


def test_worktree_absent_fixture_is_not_empty() -> None:
    text = (_FIXTURES / "worktree_absent.txt").read_text(encoding="utf-8")
    assert text.strip() != ""
