"""Evaluator wiring for the shell lane (#1581): bats + shellcheck, skip = not_run."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents.evaluator import (
    _build_all_bundles,
    _completed_functional_subtasks,
    _completed_go_subtasks,
    _completed_shell_subtasks,
    _resolve_shell_runner_fn,
)
from agents.stability_runner import StabilityVerdict
from tools.runners.docker_runner import DockerRunResult


def _plan(*subtasks: dict) -> dict:
    return {"phases": [{"phase": 1, "name": "main", "subtasks": list(subtasks)}]}


def _shell_subtask(stid: str = "st-sh-0", rel: str = "tests/rotate.bats") -> dict:
    return {
        "id": stid,
        "status": "completed",
        "lane": "unit",
        "language": "shell",
        "target": "rotate.sh",
        "rationale": "AC#1",
        "files_to_create": [rel],
    }


def _stage(spec_dir: Path, *rels: str) -> None:
    for rel in rels:
        (spec_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / rel).write_text('@test "x" {\n  true\n}\n')


def test_shell_subtask_reaches_only_the_shell_lane() -> None:
    plan = _plan(
        _shell_subtask("st-sh"), {**_shell_subtask("st-sh-p"), "status": "pending"}
    )
    assert [s["id"] for s in _completed_shell_subtasks(plan)] == ["st-sh"]
    assert _completed_functional_subtasks(plan) == []
    assert _completed_go_subtasks(plan) == []


def test_shell_runner_fails_closed_when_sandbox_unconfigured(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agents.nix_shell_lane.run_bats_lane_via_nix", lambda *a, **k: None
    )
    runner = _resolve_shell_runner_fn(tmp_path / "spec", tmp_path / "proj")
    res = runner(tmp_path / "spec" / "tests" / "x.bats", tmp_path / "proj", 0)
    assert res.returncode == 1
    assert "shell nix lane unavailable" in res.stderr


def test_build_all_bundles_runs_shell_through_the_bats_lane(
    tmp_path: Path, monkeypatch
) -> None:
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    _stage(spec_dir, "tests/rotate.bats")
    runs: list[int] = []

    def _fake(spec, proj, *, hint=None, **k):
        runs.append(1)
        return DockerRunResult(returncode=0, stdout="ok 1 x\n", argv=["bats"])

    monkeypatch.setattr("agents.nix_shell_lane.run_bats_lane_via_nix", _fake)
    shell = _completed_shell_subtasks(_plan(_shell_subtask()))
    bundles = _build_all_bundles(
        spec_dir, project_dir, [], [], [], [], [], (), (), shell
    )
    assert (project_dir / "tests" / "rotate.bats").is_file()  # staged
    assert len(bundles) == 1 and bundles[0].mutation is None
    assert bundles[0].stability.verdict == StabilityVerdict.STABLE
    assert len(runs) == 3  # one shared 3x pass


def test_all_skipped_bats_run_is_environment_not_a_rejected_test(
    tmp_path: Path, monkeypatch
) -> None:
    """#1581: exit 125 (every test skipped) classifies as not-run, never a failure."""
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    _stage(spec_dir, "tests/rotate.bats")

    def _fake(spec, proj, *, hint=None, **k):
        return DockerRunResult(
            returncode=125, stdout="ok 1 x # skip no nix\n", argv=["bats"]
        )

    monkeypatch.setattr("agents.nix_shell_lane.run_bats_lane_via_nix", _fake)
    shell = _completed_shell_subtasks(_plan(_shell_subtask()))
    bundles = _build_all_bundles(
        spec_dir, project_dir, [], [], [], [], [], (), (), shell
    )
    assert bundles[0].stability.verdict == StabilityVerdict.CONSISTENT_FAIL
    assert bundles[0].stability.failure_kind == "environment"


@pytest.mark.parametrize("padding", ["", "# padding\n" * 600])
def test_shell_bundles_grade_each_file_before_output_clipping(
    tmp_path: Path, monkeypatch, padding: str
) -> None:
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    _stage(spec_dir, "tests/fail.bats", "tests/pass.bats")
    stdout = (
        "not ok 1 broken\n__BATS_FILE_EXIT=1 ./tests/fail.bats\n"
        + padding
        + "ok 1 works\n__BATS_FILE_EXIT=0 ./tests/pass.bats\n"
        "__BATS_EXIT=1\n__SHELLCHECK_EXIT=0\n"
    )
    monkeypatch.setattr(
        "agents.nix_shell_lane.run_bats_lane_via_nix",
        lambda *a, **k: DockerRunResult(returncode=1, stdout=stdout, argv=["bats"]),
    )
    shell = [
        _shell_subtask("fail", "tests/fail.bats"),
        _shell_subtask("pass", "tests/pass.bats"),
    ]
    bundles = _build_all_bundles(
        spec_dir, project_dir, [], [], [], [], [], (), (), shell
    )
    assert [b.stability.verdict for b in bundles] == [
        StabilityVerdict.CONSISTENT_FAIL,
        StabilityVerdict.STABLE,
    ]
    assert (
        bundles[0]
        .stability.runs[0]
        .stdout_tail.startswith("FAILED tests/fail.bats: this test file failed\n")
    )
    assert all(run.stdout_tail == "" for run in bundles[1].stability.runs)


def test_shellcheck_failure_still_fails_every_file(tmp_path: Path, monkeypatch) -> None:
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    _stage(spec_dir, "tests/one.bats", "tests/two.bats")
    stdout = (
        "ok 1 one\n__BATS_FILE_EXIT=0 ./tests/one.bats\n"
        "ok 1 two\n__BATS_FILE_EXIT=0 ./tests/two.bats\n"
        "__BATS_EXIT=0\n__SHELLCHECK_EXIT=1\n"
    )
    monkeypatch.setattr(
        "agents.nix_shell_lane.run_bats_lane_via_nix",
        lambda *a, **k: DockerRunResult(returncode=1, stdout=stdout, argv=["bats"]),
    )
    shell = [
        _shell_subtask("one", "tests/one.bats"),
        _shell_subtask("two", "tests/two.bats"),
    ]
    bundles = _build_all_bundles(
        spec_dir, project_dir, [], [], [], [], [], (), (), shell
    )
    assert [b.stability.verdict for b in bundles] == [
        StabilityVerdict.CONSISTENT_FAIL,
        StabilityVerdict.CONSISTENT_FAIL,
    ]


def test_shell_file_without_result_is_error(tmp_path: Path, monkeypatch) -> None:
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    _stage(spec_dir, "tests/rotate.bats")
    stdout = (
        "ok 1 other\n__BATS_FILE_EXIT=0 ./tests/other.bats\n"
        "__BATS_EXIT=0\n__SHELLCHECK_EXIT=0\n"
    )
    monkeypatch.setattr(
        "agents.nix_shell_lane.run_bats_lane_via_nix",
        lambda *a, **k: DockerRunResult(returncode=0, stdout=stdout, argv=["bats"]),
    )
    bundles = _build_all_bundles(
        spec_dir, project_dir, [], [], [], [], [], (), (), [_shell_subtask()]
    )
    assert bundles[0].stability.verdict == StabilityVerdict.ERROR
    assert bundles[0].stability.error_message == (
        "tests/rotate.bats has no bats result: this test did not run"
    )
