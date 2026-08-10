"""Tests for the mutate-and-check probe — Task 7 (#8) commit 3.

Two surfaces under test:

  1. ``mutate_source()`` — pure AST rewrite. Verify that:
     - Compare ops inside test_* fns are flipped (==/!=/</>/<=/>=)
     - Constants (bool, int) are mutated when no Compare is available
     - Module-level constants + non-test functions are LEFT ALONE
     - SyntaxError sources return (None, None) cleanly
     - The "first applicable" rule holds (one mutation per call)

  2. ``run_mutate_probe()`` — orchestration. Verify that:
     - KILLED when the mutated test fails (runner exit ≠ 0)
     - SURVIVED when the mutated test still passes
     - NO_MUTATION when nothing was mutable
     - ERROR when runner raises, when file missing, when write fails
     - write_mutant_to mode + default-in-place mode

  3. ``mutate_source_candidates()`` / multi-function aggregation (#630):
     - one candidate per ``test_*`` function, each isolated to its own fn
     - a strong sibling test's KILLED result is not masked by a weak
       sibling's SURVIVED result
     - a genuinely weak-only file still SURVIVES (never overclaim)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from agents.mutate_probe import (
    MutationApplied,
    MutationResult,
    MutationVerdict,
    mutate_source,
    mutate_source_candidates,
    run_mutate_probe,
)

# ── Fake runner ────────────────────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# ─── mutate_source: Compare-op flips ───────────────────────────────────


def test_mutates_eq_to_noteq_inside_test_fn() -> None:
    src = "def test_x():\n    assert 1 == 1\n"
    mutated, applied = mutate_source(src)
    assert mutated is not None
    assert applied is not None
    assert applied.operator == "Eq->NotEq"
    assert applied.lineno == 2
    assert "1 != 1" in mutated  # the flipped form
    assert "1 == 1" not in mutated


def test_mutates_lt_to_gte() -> None:
    src = "def test_x():\n    assert x < 5\n"
    mutated, applied = mutate_source(src)
    assert applied is not None
    assert applied.operator == "Lt->GtE"
    assert ">= 5" in mutated


def test_mutates_only_first_compare_in_function() -> None:
    """One mutation per call. Second assert is left untouched."""
    src = "def test_x():\n    assert 1 == 1\n    assert 2 == 2\n"
    mutated, applied = mutate_source(src)
    assert applied is not None
    assert "1 != 1" in mutated
    # The second assertion stays intact.
    assert "2 == 2" in mutated
    assert "2 != 2" not in mutated


def test_mutates_in_async_test_function() -> None:
    src = "async def test_x():\n    assert True == True\n"
    mutated, applied = mutate_source(src)
    assert applied is not None
    assert applied.operator == "Eq->NotEq"


# ─── mutate_source: Constant fallback ──────────────────────────────────


def test_mutates_true_constant_when_no_compare() -> None:
    """No Compare in the test → falls through to first mutable Constant."""
    src = "def test_x():\n    assert True\n"
    mutated, applied = mutate_source(src)
    assert applied is not None
    assert applied.operator == "Constant:True->False"
    assert "assert False" in mutated


def test_mutates_int_constant_when_no_compare() -> None:
    src = "def test_x():\n    x = 5\n    assert x\n"
    mutated, applied = mutate_source(src)
    assert applied is not None
    assert applied.operator == "Constant:5->6"
    assert "x = 6" in mutated


def test_does_not_mutate_strings_or_floats() -> None:
    """Mutator only touches bool + int; strings and floats are stable
    constants that would either error or change meaning subtly."""
    src = "def test_x():\n    assert 'hello'\n    assert 3.14\n"
    mutated, applied = mutate_source(src)
    assert applied is None
    assert mutated is None


# ─── mutate_source: scope safety ───────────────────────────────────────


def test_module_level_constants_are_left_alone() -> None:
    """A test imports CONSTANT from app.config — we must NOT mutate
    the module-level CONSTANT=5 line, only the assertion inside test_*."""
    src = (
        "CONSTANT = 5\n"
        "\n"
        "def helper():\n"
        "    return 7 == 7\n"
        "\n"
        "def test_x():\n"
        "    assert CONSTANT == 5\n"
    )
    mutated, applied = mutate_source(src)
    assert applied is not None
    assert applied.operator == "Eq->NotEq"
    # Module-level CONSTANT untouched (still = 5, not = 6)
    assert "CONSTANT = 5" in mutated
    # helper() unchanged (still 7 == 7, not 7 != 7)
    assert "7 == 7" in mutated
    # Test got mutated
    assert "CONSTANT != 5" in mutated


def test_fixture_function_not_mutated() -> None:
    """@pytest.fixture is named with an _-prefix or domain word, not
    test_*. Should be skipped."""
    src = (
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def user():\n"
        "    return 1 == 1\n"
        "\n"
        "def test_uses_user(user):\n"
        "    assert user is True\n"
    )
    mutated, applied = mutate_source(src)
    # `is` is not in _COMPARE_FLIP, and `True` is a Constant.
    # So the only mutable thing is the `True` inside test_uses_user.
    assert applied is not None
    assert applied.operator == "Constant:True->False"
    # The mutation should land inside test_uses_user (line 8), not
    # inside the fixture body.
    assert applied.lineno == 8
    # fixture body intact
    assert "return 1 == 1" in mutated


# ─── mutate_source: degenerate inputs ──────────────────────────────────


def test_syntax_error_returns_none() -> None:
    mutated, applied = mutate_source("def test_x(:\n    pass\n")
    assert mutated is None
    assert applied is None


def test_no_test_functions_returns_none() -> None:
    """A module with no test_* functions has nothing to mutate."""
    src = "def helper():\n    return 1 == 1\n"
    mutated, applied = mutate_source(src)
    assert mutated is None
    assert applied is None


def test_empty_source_returns_none() -> None:
    assert mutate_source("") == (None, None)


# ─── run_mutate_probe: orchestration ───────────────────────────────────


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


def _write_test(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "test_x.py"
    f.write_text(source)
    return f


def test_killed_when_mutant_fails(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    """Runner returns non-zero → KILLED (assertion was meaningful)."""
    test_file = _write_test(
        tmp_path,
        "def test_x():\n    assert 1 == 1\n",
    )

    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stdout="assertion failed")

    result = run_mutate_probe(test_file, project_dir, _runner)
    assert isinstance(result, MutationResult)
    assert result.verdict == MutationVerdict.KILLED
    assert result.is_acceptable is True
    assert result.mutation is not None
    assert result.mutation.operator == "Eq->NotEq"
    assert result.mutated_source is not None
    assert "1 != 1" in result.mutated_source


def test_survived_when_mutant_passes(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    """Runner returns 0 → SURVIVED (assertion was tautological)."""
    test_file = _write_test(
        tmp_path,
        "def test_x():\n    assert True\n",
    )

    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=0, stdout="passed")

    result = run_mutate_probe(test_file, project_dir, _runner)
    assert result.verdict == MutationVerdict.SURVIVED
    assert result.is_acceptable is False
    assert result.mutation is not None
    assert result.mutation.operator == "Constant:True->False"


def test_no_mutation_when_nothing_to_mutate(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    test_file = _write_test(
        tmp_path,
        "def helper(): pass\n",  # no test_*, no mutations
    )

    def _runner(*_a, **_kw):
        pytest.fail("runner should not be called when no mutation applies")

    result = run_mutate_probe(test_file, project_dir, _runner)
    assert result.verdict == MutationVerdict.NO_MUTATION
    assert result.is_acceptable is True
    assert result.mutation is None


def test_missing_file_is_error(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    def _runner(*_a, **_kw):
        pytest.fail("runner should not be called when file is missing")

    result = run_mutate_probe(tmp_path / "nope.py", project_dir, _runner)
    assert result.verdict == MutationVerdict.ERROR
    assert "test file not found" in (result.error_message or "")


def test_runner_exception_is_error(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    test_file = _write_test(
        tmp_path,
        "def test_x():\n    assert 1 == 1\n",
    )

    def _runner(*_a, **_kw):
        raise RuntimeError("docker died")

    result = run_mutate_probe(test_file, project_dir, _runner)
    assert result.verdict == MutationVerdict.ERROR
    # Mutation was applied (we got past the parse step) and is preserved
    # in the error result for debugging.
    assert result.mutation is not None
    assert result.mutated_source is not None
    assert "docker died" in (result.error_message or "")


def test_write_mutant_to_writes_and_runs_against_mutant(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    test_file = _write_test(
        tmp_path,
        "def test_x():\n    assert 1 == 1\n",
    )
    mutant_path = tmp_path / "mutants" / "test_x__mut.py"
    seen_paths: list[Path] = []

    def _runner(tf, _pd, _seed):
        seen_paths.append(tf)
        return _FakeRunResult(returncode=1)

    result = run_mutate_probe(
        test_file,
        project_dir,
        _runner,
        write_mutant_to=mutant_path,
    )
    assert result.verdict == MutationVerdict.KILLED
    assert mutant_path.exists()
    # Runner saw the MUTANT path, not the original
    assert seen_paths == [mutant_path]
    # Mutant file body matches the mutated source
    assert "1 != 1" in mutant_path.read_text()


def test_seed_forwarded(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_test(
        tmp_path,
        "def test_x():\n    assert 1 == 1\n",
    )
    seen_seeds: list[int] = []

    def _runner(_tf, _pd, seed):
        seen_seeds.append(seed)
        return _FakeRunResult(returncode=1)

    run_mutate_probe(test_file, project_dir, _runner, seed=99)
    assert seen_seeds == [99]


def test_stdout_stderr_tail_truncated(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    test_file = _write_test(
        tmp_path,
        "def test_x():\n    assert 1 == 1\n",
    )
    big = "x" * 5_000 + "TAIL"

    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stdout=big, stderr=big)

    result = run_mutate_probe(test_file, project_dir, _runner, tail_chars=50)
    assert len(result.runner_stdout_tail) == 50
    assert result.runner_stdout_tail.endswith("TAIL")
    assert len(result.runner_stderr_tail) == 50


def test_mutation_applied_record_shape() -> None:
    """MutationApplied carries enough info for verdicts.json
    + debugging."""
    src = "def test_x():\n    assert 5 == 5\n"
    _, applied = mutate_source(src)
    assert isinstance(applied, MutationApplied)
    assert applied.operator == "Eq->NotEq"
    assert applied.lineno == 2
    assert "==" in applied.before
    assert "!=" in applied.after


# ─── mutate_source_candidates + strongest-signal aggregation (#630) ────
#
# Bug: a generated file can hold a WEAK test (asserts only an unrelated
# field, e.g. status_code) and a STRONG sibling test (asserts the actual
# value the acceptance criterion cares about). The old single-mutation
# probe applied ONE mutation to whichever node the AST walk hit first
# across the WHOLE file — if that landed inside the weak function's
# setup code, the mutation didn't affect that function's own assertion
# (SURVIVED), while the strong sibling — never touched, using its own
# unmutated data — still passed too. The whole file exited 0, so the
# probe reported SURVIVED even though the strong test demonstrably would
# have caught that class of mutation had it been the one mutated.


def _exec_and_run_all_tests(path: Path) -> int:
    """Minimal stand-in for ``pytest tests/<file>``: exec the module and
    call every top-level ``test_*`` function. Returns 0 if all pass, 1 if
    any raises — the same returncode contract ``run_mutate_probe`` reads.
    """
    namespace: dict = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)  # noqa: S102
    failed = False
    for name, obj in list(namespace.items()):
        if name.startswith("test_") and callable(obj):
            try:
                obj()
            except AssertionError:
                failed = True
    return 1 if failed else 0


_WEAK_AND_STRONG_SRC = (
    "def test_orders_total_standard_basket_returns_200():\n"
    "    item_price = 10  # request payload data\n"
    "    status_code = 200  # simulated response, independent of item_price\n"
    "    assert status_code == 200\n"
    "\n"
    "\n"
    "def test_orders_total_standard_basket_returns_correct_total():\n"
    "    total = 25\n"
    "    assert total == 25\n"
)


def test_mutate_source_candidates_one_per_test_function() -> None:
    """One candidate per test_* function, each isolated to its own fn."""
    candidates = mutate_source_candidates(_WEAK_AND_STRONG_SRC)
    assert len(candidates) == 2

    weak_mutated, weak_applied = candidates[0]
    assert weak_applied.operator == "Constant:10->11"
    # The strong function's body is untouched in the weak candidate.
    assert "total = 25" in weak_mutated
    assert "assert total == 25" in weak_mutated

    strong_mutated, strong_applied = candidates[1]
    assert strong_applied.operator == "Constant:25->26"
    # The weak function's body is untouched in the strong candidate.
    assert "item_price = 10" in strong_mutated
    assert "assert status_code == 200" in strong_mutated


def test_mutate_source_candidates_single_function_matches_mutate_source() -> None:
    """A single-function file reduces to exactly ``mutate_source``'s
    single-mutation result — the common case is unaffected by #630."""
    src = "def test_x():\n    assert 1 == 1\n"
    candidates = mutate_source_candidates(src)
    single_mutated, single_applied = mutate_source(src)

    assert len(candidates) == 1
    mutated, applied = candidates[0]
    assert mutated == single_mutated
    assert applied == single_applied


def test_mutate_source_candidates_empty_when_no_test_functions() -> None:
    src = "def helper():\n    return 1 == 1\n"
    assert mutate_source_candidates(src) == []


def test_mutate_source_candidates_empty_on_syntax_error() -> None:
    assert mutate_source_candidates("def test_x(:\n    pass\n") == []


def test_strong_sibling_kill_is_not_masked_by_weak_sibling_survival(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    """#630 regression: the weak test's own mutation candidate survives
    (it doesn't check the mutated value), but the strong sibling's own
    candidate IS caught by its assertion — the AC must be reported
    KILLED, not falsely SURVIVED."""
    test_file = _write_test(tmp_path, _WEAK_AND_STRONG_SRC)

    def _runner(tf, _pd, _seed):
        return _FakeRunResult(returncode=_exec_and_run_all_tests(tf))

    result = run_mutate_probe(
        test_file,
        project_dir,
        _runner,
        write_mutant_to=tmp_path / "mutant.py",
    )
    assert result.verdict == MutationVerdict.KILLED
    assert result.is_acceptable is True
    # The reported mutation is the one that actually got caught.
    assert result.mutation is not None
    assert result.mutation.operator == "Constant:25->26"


def test_weak_only_file_still_survives(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    """Never-overclaim guard: a file with ONLY the weak test (no strong
    sibling to redeem it) must still SURVIVE — #630's fix must not paper
    over a genuinely weak-only test."""
    weak_only_src = (
        "def test_orders_total_standard_basket_returns_200():\n"
        "    item_price = 10  # request payload data\n"
        "    status_code = 200  # simulated response, independent of item_price\n"
        "    assert status_code == 200\n"
    )
    test_file = _write_test(tmp_path, weak_only_src)

    def _runner(tf, _pd, _seed):
        return _FakeRunResult(returncode=_exec_and_run_all_tests(tf))

    result = run_mutate_probe(
        test_file,
        project_dir,
        _runner,
        write_mutant_to=tmp_path / "mutant.py",
    )
    assert result.verdict == MutationVerdict.SURVIVED
    assert result.is_acceptable is False


def test_run_mutate_probe_short_circuits_on_first_kill(
    tmp_path: Path,
    project_dir: Path,
) -> None:
    """Once a KILLED candidate is found, remaining functions aren't probed
    (cheaper, and the strongest signal is already conclusive)."""
    # 3 functions: killed (2nd) then a 3rd that would also be probed if we
    # didn't short-circuit.
    src = (
        "def test_a_survives():\n"
        "    x = 1  # unrelated to the assertion below\n"
        "    assert True\n"
        "\n"
        "\n"
        "def test_b_killed():\n"
        "    total = 25\n"
        "    assert total == 25\n"
        "\n"
        "\n"
        "def test_c_would_also_be_probed():\n"
        "    total = 25\n"
        "    assert total == 25\n"
    )
    test_file = _write_test(tmp_path, src)
    call_count = 0

    def _runner(tf, _pd, _seed):
        nonlocal call_count
        call_count += 1
        return _FakeRunResult(returncode=_exec_and_run_all_tests(tf))

    result = run_mutate_probe(
        test_file,
        project_dir,
        _runner,
        write_mutant_to=tmp_path / "mutant.py",
    )
    assert result.verdict == MutationVerdict.KILLED
    # Probed test_a (survived) then test_b (killed) — stopped before c.
    assert call_count == 2
