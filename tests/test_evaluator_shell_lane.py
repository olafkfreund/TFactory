"""Evaluator wiring for the shell lane (#1581): bats + shellcheck, skip = not_run."""

from __future__ import annotations

from pathlib import Path

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
