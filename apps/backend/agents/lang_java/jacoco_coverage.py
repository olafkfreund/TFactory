"""Parse a JaCoCo XML coverage report into the TFactory coverage contract.

JaCoCo (the Java coverage tool) emits ``target/site/jacoco/jacoco.xml`` with
per-line ``<line nr=".." ci=".." mi=".."/>`` entries (``ci`` = covered
instructions; a line with ci>0 is covered). This mirrors what
``coverage_delta`` consumes for Python (Cobertura) / TS (LCOV): a set of
covered ``(file, line)`` tuples + a line rate, so the Java lane's coverage
signal can be computed the same way.

Pure stdlib XML parsing — unit-testable with a sample report.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True)
class JacocoCoverage:
    covered_lines: frozenset[tuple[str, int]]  # (relative source path, line no)
    covered_count: int
    total_lines: int

    @property
    def line_rate(self) -> float:
        return (
            round(self.covered_count / self.total_lines, 4) if self.total_lines else 0.0
        )


def parse_jacoco_xml(xml_text: str) -> JacocoCoverage:
    """Parse JaCoCo XML into a JacocoCoverage. Empty on malformed input."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 — trusted local report
    except ET.ParseError:
        return JacocoCoverage(frozenset(), 0, 0)

    covered: set[tuple[str, int]] = set()
    total = 0
    for package in root.iter("package"):
        pkg = package.get("name", "")
        for sourcefile in package.iter("sourcefile"):
            fname = sourcefile.get("name", "")
            path = f"{pkg}/{fname}" if pkg else fname
            for line in sourcefile.iter("line"):
                try:
                    nr = int(line.get("nr", "0"))
                except ValueError:
                    continue
                if nr <= 0:
                    continue
                total += 1
                try:
                    ci = int(line.get("ci", "0"))
                except ValueError:
                    ci = 0
                if ci > 0:
                    covered.add((path, nr))
    return JacocoCoverage(
        covered_lines=frozenset(covered),
        covered_count=len(covered),
        total_lines=total,
    )
