#!/usr/bin/env python3
"""Tests for format-aware coverage parsing (WS4 — wire JaCoCo into coverage_delta).

The Evaluator reads a framework's coverage_strategy (jacoco for Java, cobertura
otherwise); these verify parse_coverage / compute_delta_from_paths dispatch to
the right parser so a JaCoCo report is no longer fed to the Cobertura parser.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.coverage_delta import (  # noqa: E402
    CoverageSnapshot,
    compute_delta_from_paths,
    parse_coverage,
)

_JACOCO_HALF = """<report name="r"><package name="com/ex">
  <sourcefile name="Foo.java">
    <line nr="1" ci="3" mi="0"/>
    <line nr="2" ci="0" mi="2"/>
  </sourcefile>
</package></report>"""

_JACOCO_FULL = """<report name="r"><package name="com/ex">
  <sourcefile name="Foo.java">
    <line nr="1" ci="3" mi="0"/>
    <line nr="2" ci="1" mi="0"/>
  </sourcefile>
</package></report>"""

_COBERTURA = """<coverage line-rate="0.5">
  <packages><package><classes>
    <class filename="foo.py"><lines>
      <line number="1" hits="1"/>
      <line number="2" hits="0"/>
    </lines></class>
  </classes></package></packages>
</coverage>"""


def test_parse_coverage_jacoco(tmp_path):
    p = tmp_path / "jacoco.xml"
    p.write_text(_JACOCO_HALF)
    snap = parse_coverage(p, fmt="jacoco")
    assert isinstance(snap, CoverageSnapshot)
    assert snap.covered_lines == {"com/ex/Foo.java": frozenset({1})}
    assert snap.total_lines == 2
    assert snap.line_rate == 0.5


def test_parse_coverage_defaults_to_cobertura(tmp_path):
    p = tmp_path / "coverage.xml"
    p.write_text(_COBERTURA)
    snap = parse_coverage(p)  # no fmt → cobertura
    assert snap.covered_lines == {"foo.py": frozenset({1})}


def test_jacoco_delta_detects_new_lines(tmp_path):
    base = tmp_path / "base.xml"
    after = tmp_path / "after.xml"
    base.write_text(_JACOCO_HALF)   # line 1 covered
    after.write_text(_JACOCO_FULL)  # lines 1 + 2 covered
    delta = compute_delta_from_paths(base, after, fmt="jacoco")
    assert delta.has_delta is True
    assert ("com/ex/Foo.java", 2) in delta.new_lines


def test_jacoco_no_new_lines_when_identical(tmp_path):
    base = tmp_path / "base.xml"
    after = tmp_path / "after.xml"
    base.write_text(_JACOCO_FULL)
    after.write_text(_JACOCO_FULL)
    delta = compute_delta_from_paths(base, after, fmt="jacoco")
    assert delta.has_delta is False


def test_missing_jacoco_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_coverage(tmp_path / "nope.xml", fmt="jacoco")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
