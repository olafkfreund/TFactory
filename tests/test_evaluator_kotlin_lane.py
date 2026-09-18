"""Evaluator wiring for the Kotlin/Gradle lane (Factory#1712).

Before this lane a Kotlin subtask matched no filter: the pytest filter admits
only Python, and the Go/Jest filters their own languages. It was silently
dropped, and a Kotlin-only plan fell through to ``evaluated_empty``.
"""

from __future__ import annotations

from pathlib import Path

from agents.evaluator import (
    _build_all_bundles,
    _completed_functional_subtasks,
    _completed_go_subtasks,
    _completed_kotlin_subtasks,
    _resolve_kotlin_runner_fn,
)
from tools.runners.docker_runner import DockerRunResult


def _plan(*subtasks: dict) -> dict:
    return {"phases": [{"phase": 1, "name": "main", "subtasks": list(subtasks)}]}


def _kotlin_subtask(stid: str = "st-kt-0") -> dict:
    return {
        "id": stid,
        "status": "completed",
        "lane": "unit",
        "language": "kotlin",
        "target": "Calc.add",
        "rationale": "AC#1",
        "files_to_create": ["lanes/kotlin-core/src/test/kotlin/CalcTest.kt"],
    }


def test_kotlin_subtask_reaches_only_the_kotlin_lane() -> None:
    plan = _plan(
        _kotlin_subtask("st-kt"),
        {
            "id": "st-kt-pending",
            "status": "pending",
            "lane": "unit",
            "language": "kotlin",
            "files_to_create": ["X.kt"],
        },
    )
    assert [s["id"] for s in _completed_kotlin_subtasks(plan)] == ["st-kt"]
    # Not picked up by the pytest or Go lanes.
    assert _completed_functional_subtasks(plan) == []
    assert _completed_go_subtasks(plan) == []


def test_kotlin_runner_fails_closed_when_sandbox_unconfigured(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("agents.nix_env.run_gradle_lane_via_nix", lambda *a, **k: None)
    runner = _resolve_kotlin_runner_fn(tmp_path / "spec", tmp_path / "proj")
    res = runner(tmp_path / "spec" / "X.kt", tmp_path / "proj", 0)
    assert res.returncode == 1
    assert "kotlin nix lane unavailable" in res.stderr


def test_build_all_bundles_runs_kotlin_through_the_gradle_lane(
    tmp_path: Path, monkeypatch
) -> None:
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    rel = "lanes/kotlin-core/src/test/kotlin/CalcTest.kt"
    src = spec_dir / rel
    src.parent.mkdir(parents=True)
    src.write_text("class CalcTest\n")

    calls: list[tuple[Path, Path | None]] = []

    def _fake(spec, proj, *, hint=None, **k):
        calls.append((Path(proj), hint))
        return DockerRunResult(returncode=0, stdout="BUILD SUCCESSFUL", argv=["gradle"])

    monkeypatch.setattr("agents.nix_env.run_gradle_lane_via_nix", _fake)
    kotlin = _completed_kotlin_subtasks(_plan(_kotlin_subtask()))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], kotlin)

    # The generated test was staged into the worktree at its repo path.
    assert (project_dir / rel).is_file()
    assert len(bundles) == 1 and bundles[0].test_id == "st-kt-0"
    # The Gradle Nix lane ran, with the repo-relative hint.
    assert calls and all(p == project_dir for p, _ in calls)
    assert calls[0][1] == Path(rel)


def test_kotlin_stability_is_computed_once_for_the_build(
    tmp_path: Path, monkeypatch
) -> None:
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    subs = []
    for i in range(3):
        st = _kotlin_subtask(f"st-kt-{i}")
        st["files_to_create"] = [f"src/test/kotlin/T{i}Test.kt"]
        (spec_dir / st["files_to_create"][0]).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / st["files_to_create"][0]).write_text("class T\n")
        subs.append(st)

    runs: list[int] = []

    def _fake(spec, proj, *, hint=None, **k):
        runs.append(1)
        return DockerRunResult(returncode=0, stdout="ok", argv=["gradle"])

    monkeypatch.setattr("agents.nix_env.run_gradle_lane_via_nix", _fake)
    kotlin = _completed_kotlin_subtasks(_plan(*subs))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], kotlin)
    assert len(bundles) == 3
    # One module-wide stability pass (3 reruns), not 3 per subtask.
    assert len(runs) == 3


def test_kotlin_never_goes_through_the_pytest_nix_batch(
    tmp_path: Path, monkeypatch
) -> None:
    """Factory#1712 review: in Nix mode the batched path runs pytest. A Kotlin
    subtask must use its own Gradle runner, never pytest."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    rel = "src/test/kotlin/CalcTest.kt"
    (spec_dir / rel).parent.mkdir(parents=True)
    (spec_dir / rel).write_text("class CalcTest\n")

    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda *a, **k: True)

    def _no_pytest(*a, **k):
        raise AssertionError("Kotlin was sent to the pytest Nix batch")

    monkeypatch.setattr("agents.evaluator.run_pytest_lane_via_nix", _no_pytest)
    gradle_runs: list[int] = []

    def _fake(spec, proj, *, hint=None, **k):
        gradle_runs.append(1)
        return DockerRunResult(returncode=0, stdout="ok", argv=["gradle"])

    monkeypatch.setattr("agents.nix_env.run_gradle_lane_via_nix", _fake)
    st = _kotlin_subtask()
    st["files_to_create"] = [rel]
    kotlin = _completed_kotlin_subtasks(_plan(st))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], kotlin)
    assert len(bundles) == 1
    assert gradle_runs, "the Gradle lane must have run"
    assert bundles[0].stability is not None
