#!/usr/bin/env python3
"""Coverage no-regression gate — #861.

`tfactory/coverage` has never failed a pull request. Not because coverage never
dropped, but because the gate asked the cluster for a figure that nothing ever
recorded, got an honest "I have none", and published `success` anyway. A
truthful "I did not check" is not a gate.

This measures instead of asking. The SAME pytest+coverage command runs twice in
one job -- once on the PR head, once on a worktree of the PR base -- and the two
line-coverage percentages are compared. A drop larger than the tolerance fails.

Why measure both sides here rather than read a stored baseline: a gate that
depends on state it cannot verify is how this one rotted for months. A stored
baseline also has to have been produced by an identical command to be
comparable, and nothing enforces that. Two runs in one job cost ~5 minutes and
are correct by construction -- same interpreter, same dependency set, same
flags, same runner, differing only in the tree.

The percentage is `lines-covered / lines-valid` from the Cobertura report, which
is exact; `line-rate` is the rounded fallback for a report that omits the
counts.

Usage (the workflow calls it with the backend venv's interpreter, which is then
used for both measurements):

    apps/backend/.venv/bin/python scripts/coverage_gate.py --base <base-sha>

Exit codes: 0 no drop, 1 coverage dropped, 2 the gate could not measure.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

# The measured surface: the Python we ship. Both sides use this identical list,
# so a package added by the PR counts against it exactly as new uncovered code.
COV_TARGETS = ("apps/backend", "apps/web-server/server")

# Mirrors ci.yml's `backend (ruff + pytest)` selection so the number the gate
# publishes is the coverage of the suite CI actually enforces.
PYTEST_ARGS = ("tests/", "-m", "not slow", "-q", "-p", "no:cacheprovider")

# Percentage points. Coverage is deterministic for a fixed tree, so this only
# needs to absorb the odd conditionally-skipped test, not real movement.
# ponytail: a flat tolerance; make it a ratchet on absolute line counts if
# large refactors start tripping it.
DEFAULT_TOLERANCE = 0.1

# A suite this size is ~2.5 minutes; 45 is a hang, not a slow run.
MEASURE_TIMEOUT_S = 2700

# pytest exit codes. 0 = all passed, 1 = some tests failed -- both produce a
# complete coverage report and are therefore measurable (whether tests pass is
# ci.yml's gate, not this one). 2..5 are interrupted / internal error / usage
# error / nothing collected: no usable measurement, and reporting a percentage
# derived from one is exactly the failure this gate exists to stop.
MEASURABLE_EXITS = frozenset({0, 1})

# Written outside both trees: the base worktree is an OLD commit and cannot be
# assumed to contain any file this branch adds.
_COV_CONFIG = """\
[run]
omit =
    */.venv/*
    */site-packages/*
    */node_modules/*
    */dist/*
    */build/*
    */tests/*
"""

_GIT = shutil.which("git") or "/usr/bin/git"


class GateError(RuntimeError):
    """The gate could not produce a comparison. Never downgraded to a pass."""


@dataclass(frozen=True)
class Measurement:
    """One side's line coverage."""

    pct: float
    lines_covered: int
    lines_valid: int

    def __str__(self) -> str:
        return f"{self.pct:.2f}% ({self.lines_covered}/{self.lines_valid} lines)"


def read_measurement(xml_path: Path) -> Measurement:
    """Parse a Cobertura report into a :class:`Measurement`.

    Raises :class:`GateError` rather than returning 0.0 for an empty report: a
    zero here would read as a total coverage collapse and fail the PR for what
    is really a broken measurement.
    """
    try:
        root = ET.parse(xml_path).getroot()  # noqa: S314 - our own coverage output
    except (OSError, ET.ParseError) as exc:
        raise GateError(f"unreadable coverage report {xml_path}: {exc}") from exc

    valid = int(root.get("lines-valid") or 0)
    covered = int(root.get("lines-covered") or 0)
    if valid > 0:
        return Measurement(covered * 100.0 / valid, covered, valid)

    # Older/alternative producers emit only the rate. No counts to report.
    rate = root.get("line-rate")
    if rate:
        return Measurement(float(rate) * 100.0, 0, 0)

    raise GateError(f"coverage report {xml_path} carries no line counts")


def measure(tree: Path, out_xml: Path, config: Path, label: str) -> Measurement:
    """Run the suite in *tree* with coverage and return the result.

    Output is streamed to the job log rather than captured -- when a
    measurement goes wrong the pytest output is the only thing that explains it.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *PYTEST_ARGS,
        *(f"--cov={target}" for target in COV_TARGETS),
        f"--cov-config={config}",
        f"--cov-report=xml:{out_xml}",
        # Suppress the terminal report; the XML is the artifact we consume.
        "--cov-report=",
    ]
    env = {
        **os.environ,
        "GRAPHITI_ENABLED": "false",
        "APP_DISABLE_AUTH": "true",
    }
    print(f"\n=== measuring {label} coverage in {tree} ===", flush=True)
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
            cmd, cwd=tree, env=env, timeout=MEASURE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(
            f"{label} measurement timed out after {MEASURE_TIMEOUT_S}s"
        ) from exc
    if proc.returncode not in MEASURABLE_EXITS:
        raise GateError(
            f"{label} suite exited {proc.returncode} (not a test failure -- "
            "collection, usage or internal error); no usable coverage"
        )
    return read_measurement(out_xml)


# The venv is a build artifact of the checkout, not part of it, so a worktree
# of an older commit does not have one. Some tests shell out to scripts that
# pre-flight for `apps/backend/.venv` in their own tree and fail without it --
# on the base side only, which is precisely the asymmetry this gate must not
# have. Linking it makes both sides see the same environment.
_VENV_REL = Path("apps/backend/.venv")


def link_venv(repo_root: Path, tree: Path) -> None:
    """Point *tree*'s venv path at the one measurement is running from.

    Best-effort: without it the base side loses a handful of subprocess tests,
    with it both sides are identical. A filesystem that cannot symlink is not
    worth failing the gate over.
    """
    source = repo_root / _VENV_REL
    target = tree / _VENV_REL
    if not source.is_dir() or target.exists():
        return
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)


@contextlib.contextmanager
def base_worktree(repo_root: Path, ref: str) -> Iterator[Path]:
    """Check *ref* out into a throwaway worktree beside the repo."""
    holder = Path(tempfile.mkdtemp(prefix="coverage-gate-base-"))
    tree = holder / "tree"
    add = subprocess.run(  # noqa: S603 - fixed argv
        [
            _GIT,
            "-C",
            str(repo_root),
            "worktree",
            "add",
            "--detach",
            "-q",
            str(tree),
            ref,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        shutil.rmtree(holder, ignore_errors=True)
        raise GateError(
            f"could not check out base {ref!r}: {add.stderr.strip() or add.stdout.strip()}"
        )
    link_venv(repo_root, tree)
    try:
        yield tree
    finally:
        subprocess.run(  # noqa: S603 - fixed argv
            [_GIT, "-C", str(repo_root), "worktree", "remove", "--force", str(tree)],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(holder, ignore_errors=True)


def verdict(head: Measurement, base: Measurement, tolerance: float) -> tuple[bool, str]:
    """(passed, one-line description). Descriptions stay inside the 140-char
    ceiling GitHub imposes on a commit-status description."""
    delta = head.pct - base.pct
    if delta < -tolerance:
        return False, (
            f"Coverage dropped {abs(delta):.2f} pts: {base.pct:.2f}% -> {head.pct:.2f}%"
        )
    if delta > tolerance:
        return (
            True,
            f"Coverage {head.pct:.2f}% (up {delta:.2f} pts from {base.pct:.2f}%)",
        )
    return True, f"Coverage {head.pct:.2f}% (unchanged vs base {base.pct:.2f}%)"


def emit_outputs(values: dict[str, str]) -> None:
    """Publish step outputs when running under Actions; print them regardless.

    GITHUB_OUTPUT is line-oriented, so a value carrying a newline would inject a
    bogus key. Only a GateError message can contain one (git's stderr) -- which
    is the malfunction path, the one that most needs to survive intact.
    """
    flat = {key: " ".join(str(value).split()) for key, value in values.items()}
    for key, value in flat.items():
        print(f"{key}={value}")
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with Path(out).open("a", encoding="utf-8") as fh:
        for key, value in flat.items():
            fh.write(f"{key}={value}\n")


def run_gate(repo_root: Path, base_ref: str, tolerance: float) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="coverage-gate-"))
    try:
        config = workdir / "coverage.ini"
        config.write_text(_COV_CONFIG, encoding="utf-8")

        head = measure(repo_root, workdir / "head.xml", config, "head")
        with base_worktree(repo_root, base_ref) as tree:
            base = measure(tree, workdir / "base.xml", config, "base")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    passed, description = verdict(head, base, tolerance)
    print(f"\nhead: {head}\nbase: {base}\n{description}", flush=True)
    emit_outputs(
        {
            "head_pct": f"{head.pct:.2f}",
            "base_pct": f"{base.pct:.2f}",
            "delta": f"{head.pct - base.pct:+.2f}",
            "passed": "true" if passed else "false",
            "description": description,
        }
    )
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="base commit/ref to compare against"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: the repo this script lives in)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"percentage points of allowed drop (default: {DEFAULT_TOLERANCE})",
    )
    args = parser.parse_args(argv)

    try:
        return run_gate(args.repo_root.resolve(), args.base, args.tolerance)
    except GateError as exc:
        # Exit 2, distinct from a real drop: the workflow reports "could not
        # measure" rather than "coverage dropped", and neither is a pass.
        print(f"coverage gate could not measure: {exc}", file=sys.stderr)
        emit_outputs(
            {"passed": "false", "description": f"Coverage gate failed: {exc}"[:140]}
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
