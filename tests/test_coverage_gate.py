"""The coverage gate must fail a drop — #861.

`tfactory/coverage` reported `success` on every pull request TFactory has ever
had, because it published whatever figure a regression run had recorded and no
run ever recorded one. Making that honest ("no coverage for this commit") left
it still passing everything.

The half that matters is the one that cannot be satisfied by making the gate
permissive: a measured drop has to come out red. These tests drive
``scripts/coverage_gate.py``'s comparison and report parsing directly, against
real Cobertura XML, so a regression to "always green" fails here.

The pytest invocation itself is not re-run inside pytest; ``measure`` is the
thin subprocess wrapper around it and is exercised end to end by the workflow.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "coverage_gate.py"
_spec = importlib.util.spec_from_file_location("coverage_gate", _GATE_PATH)
assert _spec and _spec.loader
coverage_gate = importlib.util.module_from_spec(_spec)
sys.modules["coverage_gate"] = coverage_gate
_spec.loader.exec_module(coverage_gate)


def _report(tmp_path: Path, name: str, covered: int, valid: int) -> Path:
    path = tmp_path / name
    rate = covered / valid if valid else 0.0
    path.write_text(
        '<?xml version="1.0" ?>\n'
        f'<coverage line-rate="{rate:.4f}" lines-covered="{covered}" '
        f'lines-valid="{valid}"><packages/></coverage>\n',
        encoding="utf-8",
    )
    return path


def _measurement(pct: float) -> coverage_gate.Measurement:
    return coverage_gate.Measurement(pct, int(pct * 100), 10000)


# -- the half that cannot be faked: a drop must be red ---------------------


def test_a_measured_drop_fails() -> None:
    passed, description = coverage_gate.verdict(
        _measurement(78.00), _measurement(81.50), coverage_gate.DEFAULT_TOLERANCE
    )
    assert passed is False, "a 3.5 point coverage drop was reported as a pass"
    assert "dropped" in description
    # The numbers, not just the word: a red gate nobody can act on gets ignored.
    assert "81.50" in description and "78.00" in description


def test_a_drop_just_past_the_tolerance_fails() -> None:
    """The boundary is where a permissive gate hides. One point past it is red."""
    passed, _ = coverage_gate.verdict(_measurement(80.0), _measurement(80.2), 0.1)
    assert passed is False


def test_exit_code_distinguishes_a_drop_from_a_broken_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drop is 1, an unmeasurable run is 2, and neither is 0."""

    def explode(*_args: object, **_kwargs: object) -> int:
        raise coverage_gate.GateError("base checkout failed")

    monkeypatch.setattr(coverage_gate, "run_gate", explode)
    assert coverage_gate.main(["--base", "deadbeef"]) == 2


# -- the other half: a clean PR gets a real percentage ---------------------


def test_unchanged_coverage_passes_with_the_figure() -> None:
    passed, description = coverage_gate.verdict(
        _measurement(81.50), _measurement(81.50), coverage_gate.DEFAULT_TOLERANCE
    )
    assert passed is True
    assert "81.50" in description, (
        "a passing gate must carry an actual percentage; a bare 'ok' is what "
        "the toothless version published"
    )


def test_improved_coverage_passes_and_says_so() -> None:
    passed, description = coverage_gate.verdict(
        _measurement(84.00), _measurement(81.50), coverage_gate.DEFAULT_TOLERANCE
    )
    assert passed is True
    assert "up 2.50" in description


def test_noise_inside_the_tolerance_passes() -> None:
    passed, _ = coverage_gate.verdict(_measurement(81.45), _measurement(81.50), 0.1)
    assert passed is True


def test_descriptions_fit_a_commit_status() -> None:
    """GitHub truncates at 140 characters; a truncated verdict is unreadable."""
    for head, base in ((78.0, 81.5), (81.5, 81.5), (84.0, 81.5)):
        _, description = coverage_gate.verdict(
            _measurement(head), _measurement(base), 0.1
        )
        assert len(description) <= 140


# -- parsing the report ----------------------------------------------------


def test_percentage_comes_from_the_exact_line_counts(tmp_path: Path) -> None:
    measurement = coverage_gate.read_measurement(_report(tmp_path, "c.xml", 3000, 4000))
    assert measurement.pct == pytest.approx(75.0)
    assert (measurement.lines_covered, measurement.lines_valid) == (3000, 4000)


def test_a_report_with_no_counts_falls_back_to_the_rate(tmp_path: Path) -> None:
    path = tmp_path / "rate-only.xml"
    path.write_text('<coverage line-rate="0.6125"/>', encoding="utf-8")
    assert coverage_gate.read_measurement(path).pct == pytest.approx(61.25)


def test_an_empty_report_raises_rather_than_reading_as_zero(tmp_path: Path) -> None:
    """0% would look like a total collapse and fail the PR for a broken
    measurement -- an error becoming a plausible wrong value (Factory#431)."""
    path = tmp_path / "empty.xml"
    path.write_text("<coverage/>", encoding="utf-8")
    with pytest.raises(coverage_gate.GateError):
        coverage_gate.read_measurement(path)


def test_an_unreadable_report_raises(tmp_path: Path) -> None:
    with pytest.raises(coverage_gate.GateError):
        coverage_gate.read_measurement(tmp_path / "does-not-exist.xml")


def test_a_failing_suite_is_still_measurable_but_a_crash_is_not() -> None:
    """pytest exit 1 (tests failed) still writes a complete report; 2..5 do not.

    Whether tests pass is ci.yml's gate. Refusing to measure a red suite would
    make this gate skip exactly the PRs most likely to be losing coverage.
    """
    assert 1 in coverage_gate.MEASURABLE_EXITS
    assert coverage_gate.MEASURABLE_EXITS.isdisjoint({2, 3, 4, 5})


def test_step_outputs_survive_a_multiline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GITHUB_OUTPUT is line-oriented. git's stderr is not, and it reaches this
    file only on the malfunction path -- the one that must stay readable."""
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    coverage_gate.emit_outputs(
        {"passed": "false", "description": "fatal: bad ref\nusage: git worktree"}
    )
    lines = out.read_text().splitlines()
    assert lines == [
        "passed=false",
        "description=fatal: bad ref usage: git worktree",
    ]


def test_the_base_tree_gets_the_same_venv_path(tmp_path: Path) -> None:
    """A worktree of an older commit has no venv, and tests that shell out to
    scripts pre-flighting for one then fail on the base side only -- an
    asymmetry between two measurements the gate reports as comparable."""
    repo, tree = tmp_path / "repo", tmp_path / "tree"
    (repo / coverage_gate._VENV_REL / "bin").mkdir(parents=True)
    tree.mkdir()

    coverage_gate.link_venv(repo, tree)
    assert (tree / coverage_gate._VENV_REL / "bin").is_dir()


def test_linking_the_venv_never_fails_the_gate(tmp_path: Path) -> None:
    """No venv to link (a plain checkout) is not an error."""
    coverage_gate.link_venv(tmp_path / "no-repo", tmp_path)


def test_the_environment_is_excluded_from_the_measurement() -> None:
    """CI has a venv inside apps/backend; counting site-packages as project code
    would swamp the figure and make the comparison meaningless."""
    for pattern in ("*/.venv/*", "*/site-packages/*", "*/node_modules/*"):
        assert pattern in coverage_gate._COV_CONFIG
