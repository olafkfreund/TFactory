"""Rust assertion-mutation probe (RFC-0010 Phase 6).

Mirrors the Python/Java/TS probes: mutate one assertion in a generated Rust test,
re-run it, and classify. A meaningful test KILLs the mutant (fails on the
mutated assertion); a tautological one lets it SURVIVE. This is the per-language
mutation signal the dispatcher wires; `cargo mutants` (source-level) is the
heavier alternative for a full mutation campaign.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from agents._mutation_common import mutate_first_assertion


class RustMutationVerdict(str, Enum):
    KILLED = "killed"  # mutant failed — assertion is meaningful
    SURVIVED = "survived"  # mutant passed — assertion is tautological / weak
    NO_MUTANT = "no_mutant"  # no mutable assertion found
    ERROR = "error"  # runner raised / couldn't classify


@dataclass(frozen=True)
class RustMutateReport:
    test_file: Path
    verdict: RustMutationVerdict
    mutated_assertion: str | None = None


_ASSERT_EQ_INT = re.compile(r"assert_eq!\(\s*([^,]+?)\s*,\s*(-?\d+)\s*\)")
_ASSERT_TRUE = re.compile(r"\bassert!\(\s*([^)]+?)\s*\)\s*;")


def _mutate_line(line: str) -> tuple[str, str] | None:
    m = _ASSERT_EQ_INT.search(line)
    if m:
        n = int(m.group(2))
        mutated = line[: m.start(2)] + str(n + 1) + line[m.end(2) :]
        return mutated, f"assert_eq! expected {n} → {n + 1}"
    m = _ASSERT_TRUE.search(line)
    if m:
        inner = m.group(1)
        mutated = line[: m.start()] + f"assert!(!({inner}));" + line[m.end() :]
        return mutated, "assert!(x) → assert!(!(x))"
    return None


def mutate_source(source: str) -> tuple[str | None, str | None]:
    """Mutate the first applicable assertion. Returns (mutated_source, desc)."""
    return mutate_first_assertion(source, _mutate_line)


def run_rust_mutate_probe(
    test_file: Path,
    project_dir: Path,
    *,
    runner_fn: Callable[..., Any] | None = None,
) -> RustMutateReport:
    """Mutate one assertion in *test_file*, run via *runner_fn*, classify.

    ``runner_fn(mutant_path, project_dir) -> result`` returns an object with a
    ``returncode`` (0 = passed → SURVIVED; non-zero = failed → KILLED). In
    production this runs ``cargo test`` in the Rust container.
    """
    try:
        source = test_file.read_text()
    except OSError:
        return RustMutateReport(test_file, RustMutationVerdict.ERROR)

    mutated, desc = mutate_source(source)
    if mutated is None:
        return RustMutateReport(test_file, RustMutationVerdict.NO_MUTANT)
    if runner_fn is None:
        return RustMutateReport(test_file, RustMutationVerdict.NO_MUTANT, desc)

    with tempfile.TemporaryDirectory() as tmp:
        mutant_path = Path(tmp) / test_file.name
        mutant_path.write_text(mutated)
        try:
            result = runner_fn(mutant_path, project_dir)
        except Exception:  # noqa: BLE001 — surface as ERROR, never crash
            return RustMutateReport(test_file, RustMutationVerdict.ERROR, desc)

    rc = getattr(result, "returncode", 1)
    verdict = RustMutationVerdict.SURVIVED if rc == 0 else RustMutationVerdict.KILLED
    return RustMutateReport(test_file, verdict, desc)
