"""The Planner's language vocabulary is derived from the framework registry (#1311).

Before this, `_EXT_LANGUAGE` and `_AC_COMMAND_LANGUAGE` were hand-kept tables
that duplicated what descriptors already declare — and the post-emit validator
already enforces `(language, framework, lane)` against that same registry. The
copies drifted: Kotlin shipped an in-cluster Gradle lane (Factory#1712) that no
plan could reach, because `.kt` mapped to nothing.

These tests pin the derivation itself. The set-equality test is the guard that
matters: a derivation that silently dropped an extension would mis-pin an
unrelated language, which is the failure #443 and #696 were filed for.
"""

from __future__ import annotations

from typing import Any

import pytest
from prompts_pkg import prompts

# The table as it stood on origin/dev before #1311, verbatim. #1311 may only
# ADD to this; any removal is a regression in an unrelated language's pinning.
_PRE_1311_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
}
_KOTLIN_ADDITIONS = {".kt": "kotlin", ".kts": "kotlin"}


def test_derived_extension_map_is_the_old_table_plus_kotlin() -> None:
    """Set equality, not a spot check: nothing was lost in the move to derivation."""
    assert prompts._build_ext_language() == {
        **_PRE_1311_EXT_LANGUAGE,
        **_KOTLIN_ADDITIONS,
    }


def test_no_extension_is_declared_both_in_a_descriptor_and_the_fallback() -> None:
    """The fallback holds only what no descriptor can supply — that is the rule.

    If a language were declared in both places they would drift again, which is
    the whole reason this issue exists.
    """
    derived: dict[str, set[str]] = {}
    for desc in prompts._registry_descriptors():
        for ext in desc.source_extensions:
            derived.setdefault(ext.lower(), set()).add(desc.language)
    overlap = set(derived) & set(prompts._NO_DESCRIPTOR_EXT_LANGUAGE)
    assert not overlap, f"declared in a descriptor AND the fallback: {sorted(overlap)}"

    ac_derived = {
        token.lower()
        for desc in prompts._registry_descriptors()
        for token in desc.ac_command_tokens
    }
    ac_overlap = ac_derived & {t for t, _ in prompts._NO_DESCRIPTOR_AC_COMMANDS}
    assert not ac_overlap, f"AC token in a descriptor AND the fallback: {ac_overlap}"


def test_a_kotlin_deliverable_pins_kotlin() -> None:
    """The gap #1311 was filed for: this returned None, so Kotlin fell through."""
    assert prompts._language_from_files(["app/src/main/kotlin/Calc.kt"]) == "kotlin"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["src/app/main.py"], "python"),
        (["cmd/server/main.go"], "go"),
        (["src/index.ts"], "typescript"),
        (["src/app.tsx"], "typescript"),
        (["src/legacy.js"], "typescript"),
        (["src/lib.rs"], "rust"),
        # Java is deliberately untouched by #1311 — the same hole is #1321, and
        # widening it here would move Java planning without a lane to prove it.
        (["src/main/java/Calc.java"], None),
        # A repo whose only changed file is JSON pins nothing. Deriving from
        # test-path globs would have mapped .json onto the cloud frameworks'
        # language and mis-pinned every package.json edit.
        (["package.json"], None),
    ],
)
def test_other_languages_pin_exactly_as_before(
    files: list[str], expected: str | None
) -> None:
    assert prompts._language_from_files(files) == expected


def test_gradle_command_in_the_acceptance_criteria_pins_kotlin() -> None:
    """An AC written in Gradle's own terms is a Kotlin signal (#1311)."""
    table = dict(prompts._build_ac_command_language())
    assert table["gradle test"] == "kotlin"
    assert table["./gradlew test"] == "kotlin"
    # The pre-existing tokens still resolve to the same languages.
    assert table["go test"] == "go"
    assert table["pytest"] == "python"
    assert table["cargo test"] == "rust"
    assert table["jest"] == "typescript"


def test_an_extension_claimed_by_two_languages_is_dropped_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguity yields no signal: a wrong pin routes to the wrong framework."""

    class _Desc:
        def __init__(self, language: str, exts: tuple[str, ...]) -> None:
            self.language = language
            self.source_extensions = exts
            self.ac_command_tokens: tuple[str, ...] = ()

    contested: list[Any] = [_Desc("kotlin", (".x",)), _Desc("go", (".x",))]
    monkeypatch.setattr(prompts, "_registry_descriptors", lambda: contested)
    assert ".x" not in prompts._build_ext_language()


def test_derivation_reads_the_registry_and_not_a_frozen_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation guard: drop Kotlin's extensions and the map must lose them.

    Without this, a derivation that quietly fell back to a hardcoded table would
    still pass every assertion above.
    """
    kept = [
        d for d in prompts._registry_descriptors() if getattr(d, "name", "") != "gradle"
    ]
    monkeypatch.setattr(prompts, "_registry_descriptors", lambda: kept)
    derived = prompts._build_ext_language()
    assert ".kt" not in derived and ".kts" not in derived
    # The descriptor-less fallback is unaffected by a registry change.
    assert derived[".rs"] == "rust"


def test_language_pinning_survives_an_unreadable_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning must degrade, never raise, if the registry cannot be read."""

    def _boom() -> Any:
        raise RuntimeError("frameworks/ unreadable")

    monkeypatch.setattr("framework_registry.load_registry", _boom)
    assert prompts._registry_descriptors() == []
    # Only the descriptor-less entries remain, and nothing raises.
    assert prompts._build_ext_language() == prompts._NO_DESCRIPTOR_EXT_LANGUAGE
