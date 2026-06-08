"""Java mutation probe — JUnit assertion mutation (#237, epic #232).

Mirrors the Python (AST) and TypeScript (Stryker) probes: apply ONE mutation to
an assertion in the generated JUnit test, re-run, and classify. If the mutated
test still passes, the assertion doesn't constrain behaviour ("survived").

Production runs the mutant via PIT (pitest) inside ``tfactory-runner-java``;
that execution is the injected ``runner_fn`` seam — tests pass a fake. The
assertion mutation itself is a lightweight regex over common JUnit forms, so
this module is unit-testable without a JVM.

Public API::

    report = run_java_mutate_probe(test_file, project_dir, runner_fn=...)
    report.verdict  # JavaMutationVerdict
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class JavaMutationVerdict(str, Enum):
    KILLED = "killed"  # mutant failed — assertion is meaningful
    SURVIVED = "survived"  # mutant passed — assertion is tautological / weak
    NO_MUTANT = "no_mutant"  # no mutable assertion found
    ERROR = "error"  # runner raised / couldn't classify


@dataclass(frozen=True)
class JavaMutateReport:
    test_file: Path
    verdict: JavaMutationVerdict
    mutated_assertion: str | None = None


# ── Assertion mutators (first applicable wins) ────────────────────────────
# Each returns (mutated_line, description) or None.

_ASSERT_EQUALS_INT = re.compile(r"assertEquals\(\s*(-?\d+)\s*,")
_ASSERT_TRUE = re.compile(r"\bassertTrue\(")
_ASSERT_FALSE = re.compile(r"\bassertFalse\(")
_TO_BE_INT = re.compile(r"isEqualTo\(\s*(-?\d+)\s*\)")  # AssertJ


def _mutate_line(line: str) -> tuple[str, str] | None:
    m = _ASSERT_EQUALS_INT.search(line)
    if m:
        n = int(m.group(1))
        mutated = line[: m.start(1)] + str(n + 1) + line[m.end(1) :]
        return mutated, f"assertEquals expected {n} → {n + 1}"
    m = _TO_BE_INT.search(line)
    if m:
        n = int(m.group(1))
        mutated = line[: m.start(1)] + str(n + 1) + line[m.end(1) :]
        return mutated, f"isEqualTo({n}) → isEqualTo({n + 1})"
    if _ASSERT_TRUE.search(line):
        return _ASSERT_TRUE.sub(
            "assertFalse(", line, count=1
        ), "assertTrue → assertFalse"
    if _ASSERT_FALSE.search(line):
        return _ASSERT_FALSE.sub(
            "assertTrue(", line, count=1
        ), "assertFalse → assertTrue"
    return None


def mutate_source(source: str) -> tuple[str | None, str | None]:
    """Mutate the first applicable assertion. Returns (mutated_source, desc)."""
    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        result = _mutate_line(line)
        if result:
            mutated_line, desc = result
            # preserve the original line ending
            if line.endswith("\n") and not mutated_line.endswith("\n"):
                mutated_line += "\n"
            lines[i] = mutated_line
            return "".join(lines), desc
    return None, None


def run_java_mutate_probe(
    test_file: Path,
    project_dir: Path,
    *,
    runner_fn: Callable[..., Any] | None = None,
) -> JavaMutateReport:
    """Mutate one assertion in *test_file*, run it via *runner_fn*, classify.

    ``runner_fn(mutant_path, project_dir) -> result`` must return an object with
    a ``returncode`` (0 = tests passed → SURVIVED; non-zero = failed → KILLED).
    In production this runs PIT/Maven in the Java container. Returns NO_MUTANT
    when no assertion is mutable, ERROR if the runner raises.
    """
    try:
        source = test_file.read_text()
    except OSError:
        return JavaMutateReport(test_file, JavaMutationVerdict.ERROR)

    mutated, desc = mutate_source(source)
    if mutated is None:
        return JavaMutateReport(test_file, JavaMutationVerdict.NO_MUTANT)
    if runner_fn is None:
        # No runner wired (e.g. unit context) — we mutated but can't execute.
        return JavaMutateReport(test_file, JavaMutationVerdict.NO_MUTANT, desc)

    with tempfile.TemporaryDirectory() as tmp:
        mutant_path = Path(tmp) / test_file.name
        mutant_path.write_text(mutated)
        try:
            result = runner_fn(mutant_path, project_dir)
        except Exception:  # noqa: BLE001 — surface as ERROR, never crash
            return JavaMutateReport(test_file, JavaMutationVerdict.ERROR, desc)

    rc = getattr(result, "returncode", 1)
    verdict = JavaMutationVerdict.SURVIVED if rc == 0 else JavaMutationVerdict.KILLED
    return JavaMutateReport(test_file, verdict, desc)
