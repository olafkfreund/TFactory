"""3× stability re-run primitive — Task 7 (#8) commit 2.

One of the FIVE evaluation signals the Evaluator (commit 5) will use:

  coverage delta · 3× stability re-run · LLM semantic relevance ·
  mutate-and-check probe · flake-lint promotion

A flaky test is one whose pass/fail status changes between identical
runs with the same seed. The 3× re-run is the cheapest cross-check
that catches the most common flake sources (timing, ordering,
non-deterministic data structures).

Verdict logic (all three runs against the same test file, same seed):

  STABLE          — all three runs exit 0
  FLAKY           — runs disagree (mix of pass/fail OR pass/error)
  CONSISTENT_FAIL — all three runs exit non-zero with the same code

The Evaluator commit-5 wiring will:
  1. For each generated test that passed the Executor's first run,
     call ``check_stability(test_file, project_dir, runner=docker_runner)``.
  2. Pass the resulting StabilityVerdict to the verdict-assembly
     prompt (FLAKY → reject, CONSISTENT_FAIL → reject, STABLE → ok).

This module exposes the docker_runner as an injected seam
(``runner_fn``) so unit tests can verify the orchestration without
spinning Docker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from agents.run_result import RunResultLike

# How many times to re-run. Three is the smallest N that can yield
# all four verdict outcomes (stable, flaky, consistent_fail, error)
# without arbitrary-tie-breaking.
RERUN_COUNT = 3

# Default fixed seed for pytest-randomly (or PYTHONHASHSEED) — same
# seed across all three runs means a deterministic test stays
# deterministic. The seed is intentionally a non-zero, non-1 value
# so it's distinguishable from "unset" or "default".
DEFAULT_SEED = 424242


class StabilityVerdict(str, Enum):
    """Outcome of a 3× re-run."""

    STABLE = "stable"
    FLAKY = "flaky"
    CONSISTENT_FAIL = "consistent_fail"
    ERROR = "error"  # the runner itself raised, not a test failure


# Shared structural result contract (extracted to agents/run_result.py, #426).
# Aliased to the historical local name so the annotation below stays unchanged.
_RunResultLike = RunResultLike


@dataclass(frozen=True)
class StabilityRun:
    """One of N re-runs in a stability check."""

    returncode: int
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# ─── Failure-kind classifier (#629) ─────────────────────────────────────
# The Evaluator's judge LLM previously had to *guess* whether a
# CONSISTENT_FAIL was an import/collection error or a genuine assertion
# failure, from the numeric ``consistent_fail`` verdict alone. With no
# deterministic signal to distinguish the two, it sometimes labelled a
# real assertion failure ("assert 0.0 == 300.0") as "the subject module
# is not resolvable/importable" — sending a human chasing a phantom
# import/co-mount bug. This helper reads the captured pytest stdout/stderr
# (deterministically, no LLM) to tell the two apart.

_IMPORT_MARKERS = (
    "ModuleNotFoundError",
    "ImportError",
    "No module named",
    "error during collection",
    "errors during collection",
    "cannot import name",
)

_ASSERTION_MARKERS = (
    "AssertionError",
    "assert ",
    "FAILED",
    "E   assert",
)

# Pytest exit codes: 0 = all passed, 1 = tests failed (assertions), 2 = usage
# error / collection error, 5 = no tests collected.
_PYTEST_COLLECTION_ERROR_CODE = 2
_PYTEST_TEST_FAILURE_CODE = 1


def classify_pytest_failure(stdout: str, returncode: int) -> str:
    """Deterministically classify *why* a pytest run failed.

    Args:
        stdout: Captured stdout (+ stderr, if the caller concatenates it) of
            the pytest invocation. Only substring markers are checked — this
            is intentionally a cheap heuristic, not a parser.
        returncode: The process exit code pytest returned.

    Returns:
        - ``"import"`` — a collection/import error: the text contains one of
          ``ModuleNotFoundError``, ``ImportError``, ``No module named``,
          ``error during collection``, ``errors during collection``,
          ``cannot import name``, OR the exit code is 2 (pytest's dedicated
          collection-error/usage-error code).
        - ``"assertion"`` — a genuine test failure: the text contains
          ``AssertionError``, ``assert ``, ``FAILED``, or ``E   assert``,
          AND the exit code is 1 (pytest's test-failure code).
        - ``"unknown"`` — neither pattern matched (includes a clean pass,
          and any failure shape this heuristic doesn't recognise).
    """
    text = stdout or ""
    if returncode == _PYTEST_COLLECTION_ERROR_CODE or any(
        marker in text for marker in _IMPORT_MARKERS
    ):
        return "import"
    if returncode == _PYTEST_TEST_FAILURE_CODE and any(
        marker in text for marker in _ASSERTION_MARKERS
    ):
        return "assertion"
    return "unknown"


@dataclass(frozen=True)
class StabilityResult:
    """Aggregate verdict + per-run record from ``check_stability``."""

    verdict: StabilityVerdict
    runs: tuple[StabilityRun, ...] = field(default_factory=tuple)
    seed: int = DEFAULT_SEED
    rerun_count: int = RERUN_COUNT
    error_message: str | None = None

    @property
    def is_acceptable(self) -> bool:
        """Convenience: did the test prove itself stable?"""
        return self.verdict == StabilityVerdict.STABLE

    @property
    def failure_kind(self) -> str | None:
        """Deterministic classification of *why* a CONSISTENT_FAIL failed (#629).

        Only meaningful for ``CONSISTENT_FAIL`` — a ``STABLE`` run has
        nothing to classify, and a ``FLAKY`` run's runs disagree on outcome
        (a mixed signal a single classification can't represent). Returns
        ``None`` in those cases, or when there are no captured runs.

        Combines every run's stdout_tail + stderr_tail (all runs share the
        same returncode for a CONSISTENT_FAIL, so this is just widening the
        marker search across whatever each run's truncated tail happened to
        retain).
        """
        if self.verdict != StabilityVerdict.CONSISTENT_FAIL or not self.runs:
            return None
        combined = "\n".join(f"{r.stdout_tail}\n{r.stderr_tail}" for r in self.runs)
        return classify_pytest_failure(combined, self.runs[0].returncode)


# ─── Public entrypoint ──────────────────────────────────────────────────


def check_stability(
    test_file: Path,
    project_dir: Path,
    runner_fn: Callable[[Path, Path, int], _RunResultLike],
    *,
    seed: int = DEFAULT_SEED,
    rerun_count: int = RERUN_COUNT,
    tail_chars: int = 500,
) -> StabilityResult:
    """Re-run ``test_file`` ``rerun_count`` times via ``runner_fn``;
    return a stability verdict.

    Args:
        test_file: Absolute path to the pytest file to re-run.
        project_dir: Project root the runner will mount (forwarded
            to ``runner_fn``).
        runner_fn: Callable that invokes the actual test runner. Sig:
            ``runner_fn(test_file, project_dir, seed) -> RunResultLike``.
            The Evaluator commit-5 wiring will pass a thin wrapper
            around docker_runner.DockerRunner.run_pytest; unit tests
            pass a fixture. Keeping this as a seam means we don't
            import docker_runner here (no circular risk).
        seed: Pytest seed passed to all 3 runs (same seed every time
            to keep determinism honest).
        rerun_count: How many times to re-run. Default 3.
        tail_chars: How many trailing chars of stdout/stderr to
            retain per run. Default 500 — enough to see a failure
            without blowing up the verdicts.json.

    Returns:
        StabilityResult capturing the verdict + per-run records.

    Raises:
        ValueError: if rerun_count < 2 (single-run can't detect flake).

    The runner_fn must not raise; any exception there bubbles up as
    a StabilityResult(verdict=ERROR, error_message=...).
    """
    if rerun_count < 2:
        raise ValueError(
            f"rerun_count must be at least 2 to detect flake; got {rerun_count}"
        )

    runs: list[StabilityRun] = []
    for _ in range(rerun_count):
        try:
            res = runner_fn(test_file, project_dir, seed)
        except Exception as exc:  # noqa: BLE001 — bubble runner errors up
            return StabilityResult(
                verdict=StabilityVerdict.ERROR,
                runs=tuple(runs),
                seed=seed,
                rerun_count=rerun_count,
                error_message=f"{type(exc).__name__}: {exc}"[:500],
            )
        runs.append(
            StabilityRun(
                returncode=res.returncode,
                stdout_tail=(res.stdout or "")[-tail_chars:],
                stderr_tail=(res.stderr or "")[-tail_chars:],
            )
        )

    codes = {r.returncode for r in runs}
    if codes == {0}:
        verdict = StabilityVerdict.STABLE
    elif len(codes) == 1:
        # All non-zero, all the same code → consistent failure.
        verdict = StabilityVerdict.CONSISTENT_FAIL
    else:
        # Mix of codes (e.g., 0 and 1, or 1 and 2) → flake.
        verdict = StabilityVerdict.FLAKY

    return StabilityResult(
        verdict=verdict,
        runs=tuple(runs),
        seed=seed,
        rerun_count=rerun_count,
    )
