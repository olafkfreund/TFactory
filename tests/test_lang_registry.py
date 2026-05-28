"""Tests for the per-language per-lane tool registry — Task 4 (#5)."""

from __future__ import annotations

import pytest

from tools.runners.lang_registry import (
    ToolSpec,
    UnsupportedLanguageError,
    get_tool_for_lane,
    languages_supporting_lane,
)


def test_python_functional_is_pytest_and_mvp_available():
    spec = get_tool_for_lane("python", "functional")
    assert spec is not None
    assert spec.binary == "pytest"
    assert spec.available_at_mvp is True


def test_python_sast_lookup_returns_unavailable_spec():
    spec = get_tool_for_lane("python", "sast")
    assert spec is not None
    assert spec.available_at_mvp is False
    assert spec.phase == "3"


def test_python_mutation_is_mutmut_phase_2():
    spec = get_tool_for_lane("python", "mutation")
    assert spec is not None
    assert spec.binary == "mutmut"
    assert spec.phase == "2"


def test_typescript_lanes_present_but_not_mvp():
    for lane in ("functional", "sast", "deps", "secrets", "mutation"):
        spec = get_tool_for_lane("typescript", lane)
        assert spec is not None, f"typescript/{lane} missing"
        assert spec.available_at_mvp is False, f"typescript/{lane} should be phase 4+"


def test_go_rust_ruby_have_no_entries_yet():
    for lang in ("go", "rust", "ruby"):
        for lane in ("functional", "sast", "mutation"):
            assert get_tool_for_lane(lang, lane) is None


def test_unsupported_language_raises():
    with pytest.raises(UnsupportedLanguageError):
        get_tool_for_lane("brainfuck", "functional")


def test_unknown_lane_returns_none_for_known_language():
    """Unknown lane on known language is a soft None, not an exception."""
    assert get_tool_for_lane("python", "telepathy") is None


def test_languages_supporting_lane_mvp_only_returns_python_for_functional():
    assert languages_supporting_lane("functional", mvp_only=True) == ["python"]


def test_languages_supporting_lane_unfiltered_returns_python_and_typescript():
    assert set(languages_supporting_lane("functional", mvp_only=False)) == {
        "python", "typescript",
    }


def test_languages_supporting_lane_mvp_only_empty_for_phase_2_3_5_lanes():
    for lane in ("sast", "mutation", "dast", "fuzz", "deps", "secrets"):
        assert languages_supporting_lane(lane, mvp_only=True) == [], f"{lane}"


def test_tool_spec_is_frozen():
    """Registry entries are immutable — accidental mutation should TypeError."""
    spec = get_tool_for_lane("python", "functional")
    with pytest.raises((AttributeError, Exception)):
        spec.binary = "nope"  # type: ignore[misc]
