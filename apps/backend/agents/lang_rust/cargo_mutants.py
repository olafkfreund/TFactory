"""Real cargo-mutants runner for Rust (RFC-0010 gap closure).

The assertion-mutation probe in :mod:`agents.lang_rust.mutate_probe` is a fast,
per-test tautology check. ``cargo mutants`` is the full source-mutation campaign:
it mutates the *source* and reports how many mutants the test suite caught vs
missed. This wraps it behind the runner_fn seam so it runs in the Rust sandbox
and parses the outcome into a verdict.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class CargoMutantsVerdict(str, Enum):
    STRONG = "strong"  # all mutants caught — tests meaningfully exercise the code
    WEAK = "weak"  # some mutants survived — gaps in the suite
    NONE = "none"  # no mutants generated (nothing to test)
    ERROR = "error"  # runner failed / unparseable


@dataclass(frozen=True)
class CargoMutantsReport:
    verdict: CargoMutantsVerdict
    caught: int = 0
    missed: int = 0
    total: int = 0

    @property
    def score(self) -> float:
        return (self.caught / self.total) if self.total else 0.0


# cargo-mutants summary line, e.g.:
#   "30 mutants tested: 27 caught, 2 missed, 1 unviable"
_SUMMARY = re.compile(
    r"(\d+)\s+mutants?\s+tested.*?(?:(\d+)\s+caught).*?(?:(\d+)\s+missed)",
    re.IGNORECASE | re.DOTALL,
)
_CAUGHT = re.compile(r"(\d+)\s+caught", re.IGNORECASE)
_MISSED = re.compile(r"(\d+)\s+missed", re.IGNORECASE)
_TESTED = re.compile(r"(\d+)\s+mutants?\s+tested", re.IGNORECASE)


def parse_cargo_mutants_output(text: str) -> CargoMutantsReport:
    """Parse cargo-mutants' textual summary into a verdict."""
    text = text or ""
    tested = _TESTED.search(text)
    if not tested:
        return CargoMutantsReport(CargoMutantsVerdict.ERROR)
    total = int(tested.group(1))
    if total == 0:
        return CargoMutantsReport(CargoMutantsVerdict.NONE, total=0)
    caught = int(_CAUGHT.search(text).group(1)) if _CAUGHT.search(text) else 0
    missed = int(_MISSED.search(text).group(1)) if _MISSED.search(text) else 0
    verdict = CargoMutantsVerdict.STRONG if missed == 0 else CargoMutantsVerdict.WEAK
    return CargoMutantsReport(verdict, caught=caught, missed=missed, total=total)


def run_cargo_mutants(
    crate_dir: Path,
    *,
    runner_fn: Callable[..., Any] | None = None,
    timeout_mutants: int = 200,
) -> CargoMutantsReport:
    """Run ``cargo mutants`` in *crate_dir* via *runner_fn* and classify.

    ``runner_fn(args, crate_dir) -> result`` runs the command in the Rust sandbox
    and returns an object exposing ``.stdout`` (and ``.returncode``). Returns
    ERROR if no runner is wired or the runner raises.
    """
    if runner_fn is None:
        return CargoMutantsReport(CargoMutantsVerdict.ERROR)
    args = ["cargo", "mutants", "--no-shuffle", "--timeout", "60", "--", "--jobs", "2"]
    try:
        result = runner_fn(args, crate_dir)
    except Exception:  # noqa: BLE001 — surface as ERROR, never crash the verify
        return CargoMutantsReport(CargoMutantsVerdict.ERROR)
    return parse_cargo_mutants_output(getattr(result, "stdout", ""))
