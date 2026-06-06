"""Tests for the Java lane wedge — JUnit + PIT + JaCoCo (#237, epic #232).

Covers the mutation probe (assertion mutation + classification via a runner
seam), the JaCoCo XML parser, the mutation dispatch routing, the descriptor,
and the language registry. No JVM/Docker — the runner is injected.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.lang_java.jacoco_coverage import parse_jacoco_xml
from agents.lang_java.mutate_probe import (
    JavaMutationVerdict,
    mutate_source,
    run_java_mutate_probe,
)
from agents.mutation_dispatch import is_mutation_supported, normalize_language

# ─── assertion mutation ──────────────────────────────────────────────────


def test_mutate_assert_equals_int():
    src = "assertEquals(5, calc());"
    mutated, desc = mutate_source(src)
    assert "assertEquals(6, calc());" in mutated
    assert "5 → 6" in desc


def test_mutate_assert_true_to_false():
    mutated, desc = mutate_source("assertTrue(x.isReady());")
    assert "assertFalse(x.isReady());" in mutated
    assert desc == "assertTrue → assertFalse"


def test_mutate_assertj_is_equal_to():
    mutated, desc = mutate_source("assertThat(n).isEqualTo(10);")
    assert "isEqualTo(11)" in mutated


def test_mutate_no_assertion():
    mutated, desc = mutate_source("int x = compute();")
    assert mutated is None and desc is None


def test_mutate_first_assertion_only():
    src = "assertEquals(1, a());\nassertEquals(2, b());\n"
    mutated, _ = mutate_source(src)
    assert "assertEquals(2, a());" in mutated  # first mutated
    assert "assertEquals(2, b());" in mutated  # second untouched


# ─── probe verdicts via runner seam ──────────────────────────────────────


def _write(tmp_path, body):
    f = tmp_path / "FooTest.java"
    f.write_text(body)
    return f


def test_probe_killed_when_mutant_fails(tmp_path):
    f = _write(tmp_path, "assertEquals(5, calc());")
    rep = run_java_mutate_probe(
        f, tmp_path, runner_fn=lambda m, p: SimpleNamespace(returncode=1)
    )
    assert rep.verdict == JavaMutationVerdict.KILLED


def test_probe_survived_when_mutant_passes(tmp_path):
    f = _write(tmp_path, "assertEquals(5, calc());")
    rep = run_java_mutate_probe(
        f, tmp_path, runner_fn=lambda m, p: SimpleNamespace(returncode=0)
    )
    assert rep.verdict == JavaMutationVerdict.SURVIVED


def test_probe_no_mutant_when_no_assertion(tmp_path):
    f = _write(tmp_path, "int x = 1;")
    rep = run_java_mutate_probe(f, tmp_path, runner_fn=lambda m, p: SimpleNamespace(returncode=1))
    assert rep.verdict == JavaMutationVerdict.NO_MUTANT


def test_probe_error_when_runner_raises(tmp_path):
    f = _write(tmp_path, "assertEquals(5, calc());")

    def boom(m, p):
        raise RuntimeError("pit blew up")

    rep = run_java_mutate_probe(f, tmp_path, runner_fn=boom)
    assert rep.verdict == JavaMutationVerdict.ERROR


def test_probe_no_runner_returns_no_mutant(tmp_path):
    f = _write(tmp_path, "assertEquals(5, calc());")
    rep = run_java_mutate_probe(f, tmp_path)  # no runner
    assert rep.verdict == JavaMutationVerdict.NO_MUTANT
    assert rep.mutated_assertion is not None  # it did find a mutation


# ─── dispatch routing ────────────────────────────────────────────────────


def test_dispatch_supports_java():
    assert is_mutation_supported("java") is True
    assert normalize_language("java") == "java"


def test_run_language_mutation_routes_java(tmp_path):
    from agents.mutation_dispatch import run_language_mutation

    f = _write(tmp_path, "assertEquals(5, calc());")
    rep = run_language_mutation(
        "java", f, tmp_path,
        runner_fn=lambda m, p: SimpleNamespace(returncode=1),
        mutant_path=tmp_path / "m.java",
    )
    assert rep.verdict == JavaMutationVerdict.KILLED


# ─── JaCoCo coverage parser ──────────────────────────────────────────────

_JACOCO = """<?xml version="1.0"?>
<report name="demo">
  <package name="com/example">
    <sourcefile name="Calc.java">
      <line nr="3" mi="0" ci="4"/>
      <line nr="4" mi="2" ci="0"/>
      <line nr="5" mi="0" ci="1"/>
    </sourcefile>
  </package>
</report>
"""


def test_parse_jacoco_covered_lines():
    cov = parse_jacoco_xml(_JACOCO)
    assert ("com/example/Calc.java", 3) in cov.covered_lines
    assert ("com/example/Calc.java", 5) in cov.covered_lines
    assert ("com/example/Calc.java", 4) not in cov.covered_lines  # ci=0
    assert cov.covered_count == 2
    assert cov.total_lines == 3
    assert cov.line_rate == round(2 / 3, 4)


def test_parse_jacoco_malformed():
    cov = parse_jacoco_xml("not xml <<<")
    assert cov.covered_count == 0 and cov.total_lines == 0
    assert cov.line_rate == 0.0


# ─── descriptor + registry ───────────────────────────────────────────────


def test_junit_descriptor_loads():
    from framework_registry.loader import get_descriptor

    real = Path(__file__).parent.parent / "frameworks"
    desc = get_descriptor("junit", frameworks_dir=real)
    assert desc.language == "java"
    assert any("unit" in str(lane_).lower() for lane_ in desc.lanes)
    assert desc.runtime.image == "tfactory-runner-java:latest"
    assert desc.coverage_strategy == "jacoco"


def test_registry_has_java():
    from tools.runners.lang_registry import get_tool_for_lane

    assert get_tool_for_lane("java", "unit") is not None
    assert get_tool_for_lane("java", "mutation") is not None
