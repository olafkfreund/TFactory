"""Descriptor-declared languages in the lane registry.

The supported registry rows come from the vendored
``tools/runners/languages/*.yaml`` descriptors (hub canonical
``Factory contracts/languages/``), not from :data:`lang_registry._REGISTRY`.
These tests pin the properties that matter:

1. A supported lane the descriptor proves runnable gets a ToolSpec (kotlin
   unit/api via junit; kotlin mutation via pit).

2. A lane the descriptor declares unavailable is structurally unrunnable
   (``None`` - no ToolSpec, no command, nothing whose exit code could read as
   a verdict) AND its mandatory reason is machine-readable via
   :func:`unavailable_lane_reason`, feeding RFC-0006 VAL-0 instead of a silent
   omission.

3. Swift is explicitly refused even though its vendored descriptor remains
   readable for unavailable-lane reasons.
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


def test_swift_is_refused_despite_its_descriptor() -> None:
    with pytest.raises(UnsupportedLanguageError, match="#1340"):
        get_tool_for_lane("swift", "unit")


def test_kotlin_lanes_registered_from_the_descriptor() -> None:
    unit = get_tool_for_lane("kotlin", "unit")
    mutation = get_tool_for_lane("kotlin", "mutation")
    assert unit is not None and unit.binary == "junit"
    assert "gradle test" in unit.description
    assert mutation is not None and mutation.binary == "pit"


def test_swift_unavailable_lanes_keep_their_descriptor_reasons() -> None:
    for lane in ("browser", "mutation", "integration", "api"):
        with pytest.raises(UnsupportedLanguageError, match="#1340"):
            get_tool_for_lane("swift", lane)
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
    assert "swift" not in unit_langs and "kotlin" in unit_langs
    browser_langs = languages_supporting_lane("browser")
    assert "swift" not in browser_langs and "kotlin" not in browser_langs


def test_negative_control_unwired_descriptors_refuse_kotlin(monkeypatch) -> None:
    """Unwire the descriptor rows; kotlin must go RED, not quietly vanish."""
    monkeypatch.setattr(lang_registry, "_descriptor_registry", lambda: {})
    with pytest.raises(UnsupportedLanguageError):
        get_tool_for_lane("kotlin", "unit")


def test_shell_unit_lane_registered_from_the_local_descriptor() -> None:
    """#1581: bats is the shell unit lane; every other lane says why it is off."""
    unit = get_tool_for_lane("shell", "unit")
    assert unit is not None and unit.binary == "bats"
    for lane in ("browser", "api", "integration", "mutation"):
        assert get_tool_for_lane("shell", lane) is None
        assert unavailable_lane_reason("shell", lane), f"shell.{lane} needs a reason"
    assert unavailable_lane_reason("shell", "unit") is None


def test_shell_aliases_resolve_to_the_same_descriptor() -> None:
    from tools.runners.language_descriptors import resolve_language

    names = {resolve_language(a).name for a in ("shell", "bash", "sh")}
    assert names == {"shell"}


def test_generated_flake_for_shell_carries_bats_and_shellcheck() -> None:
    """generate_flake fails closed for an unknown language; shell is now known."""
    from tools.runners.nix_provisioner import generate_flake

    flake = generate_flake({"language": "shell", "verify_commands": ["bats tests"]})
    assert "pkgs.bats" in flake and "pkgs.shellcheck" in flake
