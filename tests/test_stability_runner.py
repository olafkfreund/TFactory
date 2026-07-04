"""Tests for the 3× stability runner primitive — Task 7 (#8) commit 2.

The runner is mocked at the ``runner_fn`` seam — these tests verify
the verdict logic without spinning Docker. The integration with
docker_runner.DockerRunner.run_pytest happens in commit 5 of Task 7.

Covered:
  - All three runs exit 0 → STABLE
  - All three runs same non-zero code → CONSISTENT_FAIL
  - Mixed codes → FLAKY (0/1, 1/0/1, 1/2/3 — anything heterogeneous)
  - runner_fn raises → ERROR with message captured
  - Configurable rerun_count (e.g., 2, 5)
  - Invalid rerun_count rejected
  - Seed forwarded to runner_fn
  - stdout/stderr tail captured + truncated correctly
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from agents.stability_runner import (
    DEFAULT_SEED,
    RERUN_COUNT,
    StabilityResult,
    StabilityRun,
    StabilityVerdict,
    check_stability,
    classify_pytest_failure,
)

# ── Fake DockerRunResult ───────────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# ── Test fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def test_file(tmp_path: Path) -> Path:
    f = tmp_path / "test_x.py"
    f.write_text("def test_x(): pass\n")
    return f


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


# ── Verdict logic ──────────────────────────────────────────────────────


def test_all_three_pass_is_stable(test_file: Path, project_dir: Path) -> None:
    calls: list[int] = []
    def _runner(_tf, _pd, seed):
        calls.append(seed)
        return _FakeRunResult(returncode=0, stdout="passed")

    result = check_stability(test_file, project_dir, _runner)
    assert isinstance(result, StabilityResult)
    assert result.verdict == StabilityVerdict.STABLE
    assert result.is_acceptable is True
    assert len(result.runs) == 3
    assert all(r.ok for r in result.runs)
    assert calls == [DEFAULT_SEED] * 3


def test_all_three_fail_same_code_is_consistent(
    test_file: Path, project_dir: Path,
) -> None:
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stdout="failed")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.CONSISTENT_FAIL
    assert result.is_acceptable is False
    assert all(not r.ok for r in result.runs)


def test_mixed_pass_fail_is_flaky(test_file: Path, project_dir: Path) -> None:
    """Two passes + one fail → FLAKY (the most common real-world shape)."""
    sequence = iter([0, 1, 0])
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=next(sequence))

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.FLAKY
    assert result.is_acceptable is False
    codes = [r.returncode for r in result.runs]
    assert codes == [0, 1, 0]


def test_two_different_non_zero_codes_is_flaky(
    test_file: Path, project_dir: Path,
) -> None:
    """Different *kinds* of failure across runs are still FLAKY,
    not CONSISTENT_FAIL — the test isn't deterministically wrong,
    it's nondeterministically wrong."""
    sequence = iter([1, 2, 1])
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=next(sequence))

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.FLAKY


def test_runner_exception_is_error(test_file: Path, project_dir: Path) -> None:
    """If the runner itself blows up, verdict is ERROR — distinct from
    test failure. The Evaluator's verdict logic treats this as
    'inconclusive' (likely a sandbox issue, not the test's fault)."""
    def _runner(_tf, _pd, _seed):
        raise RuntimeError("docker socket missing")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.ERROR
    assert result.is_acceptable is False
    assert "docker socket missing" in (result.error_message or "")
    assert "RuntimeError" in (result.error_message or "")
    # Runs collected up to the failing call are preserved (none here).
    assert result.runs == ()


def test_runner_exception_after_partial_runs(
    test_file: Path, project_dir: Path,
) -> None:
    """If the second run blows up, the first run's record is preserved."""
    sequence = iter([
        _FakeRunResult(returncode=0),
        # The next iteration raises StopIteration; we want a different
        # exception type to confirm it's captured.
    ])
    call_count = {"n": 0}
    def _runner(_tf, _pd, _seed):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeRunResult(returncode=0)
        raise ValueError("transient docker failure")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.ERROR
    assert len(result.runs) == 1
    assert result.runs[0].ok is True
    assert "transient docker failure" in (result.error_message or "")


# ── Configurability ────────────────────────────────────────────────────


def test_rerun_count_two_allowed(test_file: Path, project_dir: Path) -> None:
    calls = []
    def _runner(_tf, _pd, _seed):
        calls.append(1)
        return _FakeRunResult(returncode=0)

    result = check_stability(test_file, project_dir, _runner, rerun_count=2)
    assert result.verdict == StabilityVerdict.STABLE
    assert len(result.runs) == 2
    assert result.rerun_count == 2


def test_rerun_count_five_runs_five_times(
    test_file: Path, project_dir: Path,
) -> None:
    calls = []
    def _runner(_tf, _pd, _seed):
        calls.append(1)
        return _FakeRunResult(returncode=0)

    result = check_stability(test_file, project_dir, _runner, rerun_count=5)
    assert len(calls) == 5
    assert len(result.runs) == 5


def test_rerun_count_one_rejected(test_file: Path, project_dir: Path) -> None:
    """A single run can't detect flake — must be at least 2."""
    def _runner(*a, **kw): return _FakeRunResult(returncode=0)
    with pytest.raises(ValueError, match="at least 2"):
        check_stability(test_file, project_dir, _runner, rerun_count=1)


def test_custom_seed_forwarded(test_file: Path, project_dir: Path) -> None:
    seen_seeds: list[int] = []
    def _runner(_tf, _pd, seed):
        seen_seeds.append(seed)
        return _FakeRunResult(returncode=0)

    result = check_stability(test_file, project_dir, _runner, seed=12345)
    assert seen_seeds == [12345, 12345, 12345]
    assert result.seed == 12345


def test_default_rerun_count_is_three() -> None:
    """The default of three is the documented convention; lock it down."""
    assert RERUN_COUNT == 3


# ── Output capture ─────────────────────────────────────────────────────


def test_stdout_tail_truncated(test_file: Path, project_dir: Path) -> None:
    """Long stdout is truncated to ``tail_chars`` to keep verdicts.json
    a reasonable size."""
    big_stdout = "x" * 10_000 + "TAIL_MARKER"
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=0, stdout=big_stdout)

    result = check_stability(test_file, project_dir, _runner, tail_chars=100)
    for r in result.runs:
        assert len(r.stdout_tail) == 100
        assert r.stdout_tail.endswith("TAIL_MARKER")


def test_stderr_tail_truncated(test_file: Path, project_dir: Path) -> None:
    big_stderr = "e" * 5_000 + "STDERR_TAIL"
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stderr=big_stderr)

    result = check_stability(test_file, project_dir, _runner, tail_chars=50)
    for r in result.runs:
        assert len(r.stderr_tail) == 50
        assert r.stderr_tail.endswith("STDERR_TAIL")


def test_empty_stdout_stderr_handled(test_file: Path, project_dir: Path) -> None:
    """Some runners may return empty strings (or None — guarded)."""
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=0, stdout="", stderr="")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.STABLE
    assert all(r.stdout_tail == "" for r in result.runs)
    assert all(r.stderr_tail == "" for r in result.runs)


# ── StabilityRun helper properties ─────────────────────────────────────


def test_stability_run_ok_property() -> None:
    assert StabilityRun(returncode=0).ok is True
    assert StabilityRun(returncode=1).ok is False
    assert StabilityRun(returncode=2).ok is False


# ── classify_pytest_failure (#629) ─────────────────────────────────────
# The Evaluator's judge LLM used to guess "import error" for a
# consistent_fail that was actually a real assertion failure. These pin
# the deterministic classifier against realistic pytest stdout/stderr
# shapes so that regression is caught by unit tests, not a confused human.

_ASSERTION_FAILURE_STDOUT = """
============================= test session starts ==============================
collected 6 items

tests/test_orders.py::test_total_price FAILED                          [ 16%]
tests/test_orders.py::test_line_items FAILED                           [ 33%]

=================================== FAILURES ====================================
_______________________________ test_total_price ________________________________

    def test_total_price():
>       assert 0.0 == 300.0
E       assert 0.0 == 300.0

tests/test_orders.py:42: AssertionError
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_total_price - assert 0.0 == 300.0
FAILED tests/test_orders.py::test_line_items - assert 0.0 == 300.0
============================== 6 failed in 0.34s ================================
"""

_IMPORT_ERROR_STDOUT = """
============================= test session starts ==============================
collected 0 items / 1 error

==================================== ERRORS ======================================
______________________ ERROR collecting tests/test_orders.py ______________________
ImportError while importing test module '/app/tests/test_orders.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
../../.venv/lib/python3.11/site-packages/_pytest/python.py:493: in import_module
    mod = import_path(...)
E   ModuleNotFoundError: No module named 'orders_api'
=========================== short test summary info ============================
ERROR tests/test_orders.py
=============================== 1 error during collection ========================
"""

_CLEAN_SUCCESS_STDOUT = """
============================= test session starts ==============================
collected 6 items

tests/test_orders.py::test_total_price PASSED                          [ 16%]
tests/test_orders.py::test_line_items PASSED                           [ 33%]

============================== 6 passed in 0.12s ================================
"""


def test_classify_assertion_failure() -> None:
    """A genuine assertion failure (exit 1) is classified 'assertion', not
    mislabelled as an import error (#629 — the demo hardcode bug: pytest
    output `assert 0.0 == 300.0`, `6 failed`)."""
    assert classify_pytest_failure(_ASSERTION_FAILURE_STDOUT, 1) == "assertion"


def test_classify_import_error_via_marker() -> None:
    assert classify_pytest_failure(_IMPORT_ERROR_STDOUT, 2) == "import"


def test_classify_import_error_by_exit_code_alone() -> None:
    """Exit code 2 (pytest's collection-error code) is import even without a
    recognised marker string — the exit code itself is the strongest signal."""
    assert (
        classify_pytest_failure("some unrecognised collection failure", 2) == "import"
    )


@pytest.mark.parametrize(
    "marker",
    [
        "ModuleNotFoundError: No module named 'orders_api'",
        "ImportError: cannot import name 'Foo' from 'bar'",
        "No module named 'orders_api'",
        "1 error during collection",
        "errors during collection",
        "cannot import name 'Foo'",
    ],
)
def test_classify_import_markers(marker: str) -> None:
    assert classify_pytest_failure(marker, 1) == "import"


def test_classify_assertion_requires_exit_code_one() -> None:
    """Assertion markers alone (without exit code 1) don't classify as
    'assertion' — e.g. exit code 2 always wins as 'import' regardless of
    stray text that happens to contain 'assert'."""
    assert classify_pytest_failure(_ASSERTION_FAILURE_STDOUT, 2) == "import"


def test_classify_unrecognised_failure_is_unknown() -> None:
    assert classify_pytest_failure("boom, something else broke", 3) == "unknown"


def test_classify_clean_pass_is_not_misclassified() -> None:
    """A clean passing run must never be classified as 'import' or
    'assertion' — there's no failure to classify."""
    assert classify_pytest_failure(_CLEAN_SUCCESS_STDOUT, 0) == "unknown"


def test_classify_empty_stdout() -> None:
    assert classify_pytest_failure("", 1) == "unknown"
    assert classify_pytest_failure("", 0) == "unknown"


# ── StabilityResult.failure_kind (#629) ────────────────────────────────


def test_failure_kind_none_when_stable() -> None:
    result = StabilityResult(
        verdict=StabilityVerdict.STABLE,
        runs=(StabilityRun(returncode=0), StabilityRun(returncode=0)),
    )
    assert result.failure_kind is None


def test_failure_kind_none_when_flaky() -> None:
    result = StabilityResult(
        verdict=StabilityVerdict.FLAKY,
        runs=(StabilityRun(returncode=0), StabilityRun(returncode=1)),
    )
    assert result.failure_kind is None


def test_failure_kind_none_when_error() -> None:
    result = StabilityResult(verdict=StabilityVerdict.ERROR, runs=())
    assert result.failure_kind is None


def test_failure_kind_assertion_for_consistent_fail(
    test_file: Path,
    project_dir: Path,
) -> None:
    """End-to-end via check_stability: all three runs fail with a real
    assertion → failure_kind='assertion', not a phantom import guess."""

    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stdout=_ASSERTION_FAILURE_STDOUT)

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.CONSISTENT_FAIL
    assert result.failure_kind == "assertion"


def test_failure_kind_import_for_consistent_fail(
    test_file: Path,
    project_dir: Path,
) -> None:
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=2, stdout=_IMPORT_ERROR_STDOUT)

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.CONSISTENT_FAIL
    assert result.failure_kind == "import"


def test_failure_kind_checks_stderr_too(
    test_file: Path,
    project_dir: Path,
) -> None:
    """The classifier looks at stderr as well as stdout — some pytest
    configurations route the traceback to stderr."""

    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(
            returncode=2,
            stdout="",
            stderr=_IMPORT_ERROR_STDOUT,
        )

    result = check_stability(test_file, project_dir, _runner)
    assert result.failure_kind == "import"
