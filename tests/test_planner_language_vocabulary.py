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
# #1321 gave Java an in-cluster lane (frameworks/maven), so .java pins now. It
# arrived purely from dropping in that descriptor — no edit to prompts.py, which
# is what the derivation exists for.
_JAVA_ADDITIONS = {".java": "java"}


def test_derived_extension_map_is_the_old_table_plus_kotlin_and_java() -> None:
    """Set equality, not a spot check: nothing was lost in the move to derivation."""
    assert prompts._build_ext_language() == {
        **_PRE_1311_EXT_LANGUAGE,
        **_KOTLIN_ADDITIONS,
        **_JAVA_ADDITIONS,
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
        # Java pins since #1321 gave it a lane to run in (frameworks/maven).
        (["src/main/java/Calc.java"], "java"),
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


def test_registry_block_states_each_framework_s_test_paths() -> None:
    """The prompt's file-naming rule points at the descriptor (#1311).

    Kotlin's row must carry Gradle's conventional path, or the Planner would
    have to infer where a Kotlin test belongs — and a test written outside
    ``src/test/kotlin`` is not run by ``gradle test`` at all.
    """
    block = prompts._build_framework_registry_block()
    gradle_row = next(
        line for line in block.splitlines() if line.startswith("- gradle:")
    )
    assert "language=kotlin" in gradle_row
    assert "src/test/kotlin" in gradle_row
    # Pre-existing rows keep their language and gain their own conventions.
    go_row = next(line for line in block.splitlines() if line.startswith("- go-test:"))
    assert "language=go" in go_row and "*_test.go" in go_row


def test_registry_block_stays_small_enough_to_inject() -> None:
    """It is prepended to every planning prompt; a runaway block costs tokens."""
    assert len(prompts._build_framework_registry_block()) < 4000


def _pin_block(tmp_path: Any, changed: list[str], ac_text: str = "") -> str:
    """Render the DETECTED PROJECT LANGUAGE block for a given changed-file set."""
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True, exist_ok=True)
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    patch = "".join(
        f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -0,0 +1 @@\n+x\n"
        for f in changed
    )
    (spec / "context" / "diff.patch").write_text(patch)
    (spec / "context" / "aifactory_spec.md").write_text(ac_text or "AC#1: it works.\n")
    return prompts._build_detected_language_block(spec, proj)


@pytest.mark.parametrize(
    ("changed", "expected_language"),
    [
        (["src/app/main.py"], "python"),
        (["src/index.ts"], "typescript"),
        (["cmd/server/main.go"], "go"),
    ],
)
def test_existing_languages_still_pin_the_same_way(
    tmp_path: Any, changed: list[str], expected_language: str
) -> None:
    """#1311 must not move an existing language's deterministic pin (#443, #696)."""
    block = _pin_block(tmp_path, changed)
    assert f"**{expected_language}** project" in block


def test_a_kotlin_diff_now_pins_kotlin_and_names_gradle(tmp_path: Any) -> None:
    """The behaviour #1311 delivers, at the block the Planner actually reads.

    Before: "No deterministic language signal ... never assume pytest."
    """
    block = _pin_block(tmp_path, ["app/src/main/kotlin/Calc.kt"])
    assert "**kotlin** project" in block
    assert "framework: gradle" in block


def test_a_java_diff_now_pins_java_and_names_maven(tmp_path: Any) -> None:
    """#1321 replaced #1311's deliberate omission with a real lane.

    Before: "No deterministic language signal ... never assume pytest."
    """
    block = _pin_block(tmp_path, ["src/main/java/Calc.java"])
    assert "**java** project" in block
    assert "framework: maven" in block


def test_a_maven_acceptance_criterion_pins_java(tmp_path: Any) -> None:
    """An AC written in Maven's terms is a Java signal (#1321)."""
    block = _pin_block(tmp_path, [], ac_text="AC#1: `mvn -B test` passes.\n")
    assert "**java** project" in block


def test_a_gradle_acceptance_criterion_pins_kotlin_without_any_diff(
    tmp_path: Any,
) -> None:
    """AC commands are the signal when no diff is available (#443's path)."""
    block = _pin_block(tmp_path, [], ac_text="AC#1: `gradle test` passes.\n")
    assert "**kotlin** project" in block
