"""Descriptor-declared languages in the lane registry (Swift, Kotlin).

The registry rows for these languages come from the vendored
``tools/runners/languages/*.yaml`` descriptors (hub canonical
``Factory contracts/languages/``), not from :data:`lang_registry._REGISTRY`.
These tests pin the three properties that matter:

1. A lane the descriptor proves runnable gets a ToolSpec (swift unit via
   xctest; kotlin unit/api via junit; kotlin mutation via pit). The commands
   were RUN on this substrate before the descriptors said so: a minimal SPM
   package and Gradle module passed - and, mutated, failed - inside
   ``nix develop`` of the provisioner-generated flakes (2026-09-03).

2. A lane the descriptor declares unavailable is structurally unrunnable
   (``None`` - no ToolSpec, no command, nothing whose exit code could read as
   a verdict) AND its mandatory reason is machine-readable via
   :func:`unavailable_lane_reason`, feeding RFC-0006 VAL-0 instead of a silent
   omission.

3. The negative control: with the descriptor path unwired, swift must be
   REFUSED (UnsupportedLanguageError), so a registry that never read the
   descriptors cannot produce the same results as one that did.
"""

from __future__ import annotations

import pytest
from tools.runners import lang_registry
from tools.runners.lang_registry import (
    UnsupportedLanguageError,
    get_tool_for_lane,
    languages_supporting_lane,
    unavailable_lane_reason,
)


def test_swift_unit_lane_is_registered_from_the_descriptor() -> None:
    spec = get_tool_for_lane("swift", "unit")
    assert spec is not None
    assert spec.binary == "xctest"
    assert "swift test" in spec.description


def test_kotlin_lanes_registered_from_the_descriptor() -> None:
    unit = get_tool_for_lane("kotlin", "unit")
    mutation = get_tool_for_lane("kotlin", "mutation")
    assert unit is not None and unit.binary == "junit"
    assert "gradle test" in unit.description
    assert mutation is not None and mutation.binary == "pit"


def test_swift_unavailable_lanes_are_none_with_a_reason() -> None:
    """An unavailable lane can NEVER be reported as passed: there is no
    ToolSpec, hence no command, hence nothing to run - and the WHY is
    machine-readable rather than a comment someone had to remember."""
    for lane in ("browser", "mutation", "integration", "api"):
        assert get_tool_for_lane("swift", lane) is None, lane
        reason = unavailable_lane_reason("swift", lane)
        assert reason, f"swift.{lane} must carry its reason"
    # The native-UI honesty case (not a registry lane key, still declared):
    # XCUITest needs macOS and the descriptor says so.
    assert "macOS" in (unavailable_lane_reason("swift", "ui") or "")


def test_available_lane_has_no_unavailability_reason() -> None:
    assert unavailable_lane_reason("swift", "unit") is None
    assert unavailable_lane_reason("kotlin", "unit") is None
    # A language with no descriptor says nothing (None), it does not invent.
    assert unavailable_lane_reason("python", "unit") is None


def test_static_registry_rows_stay_authoritative() -> None:
    spec = get_tool_for_lane("python", "unit")
    assert spec is not None and spec.binary == "pytest"


def test_languages_supporting_lane_includes_descriptor_languages() -> None:
    unit_langs = languages_supporting_lane("unit")
    assert "swift" in unit_langs and "kotlin" in unit_langs
    # Honesty in the other direction: neither may claim a browser lane.
    browser_langs = languages_supporting_lane("browser")
    assert "swift" not in browser_langs and "kotlin" not in browser_langs


def test_negative_control_unwired_descriptors_refuse_swift(monkeypatch) -> None:
    """Unwire the descriptor rows; swift must go RED, not quietly vanish.

    A registry that answers identically with and without the descriptors would
    make every assertion above a pass-shaped empty measurement.
    """
    monkeypatch.setattr(lang_registry, "_descriptor_registry", lambda: {})
    with pytest.raises(UnsupportedLanguageError):
        get_tool_for_lane("swift", "unit")
