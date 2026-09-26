"""Evaluator wiring for the Java/Maven lane (#1321).

Java was in Kotlin's pre-#1712 position: no lane filter admitted it, so a Java
subtask was silently dropped and a Java-only plan ended as ``evaluated_empty``.
#1311 left it that way deliberately — there was no in-cluster lane to route it
to until ``frameworks/maven``.
"""

from __future__ import annotations

from pathlib import Path

from agents.evaluator import (
    _build_all_bundles,
    _completed_functional_subtasks,
    _completed_java_subtasks,
    _completed_kotlin_subtasks,
    _resolve_java_runner_fn,
)
from tools.runners.docker_runner import DockerRunResult


def _plan(*subtasks: dict) -> dict:
    return {"phases": [{"phase": 1, "name": "main", "subtasks": list(subtasks)}]}


def _java_subtask(stid: str = "st-jv-0") -> dict:
    return {
        "id": stid,
        "status": "completed",
        "lane": "unit",
        "language": "java",
        "target": "Calc.add",
        "rationale": "AC#1",
        "files_to_create": ["src/test/java/CalcTest.java"],
    }


def test_java_subtask_reaches_only_the_java_lane() -> None:
    plan = _plan(
        _java_subtask("st-jv"),
        {
            "id": "st-jv-pending",
            "status": "pending",
            "lane": "unit",
            "language": "java",
            "files_to_create": ["X.java"],
        },
    )
    assert [s["id"] for s in _completed_java_subtasks(plan)] == ["st-jv"]
    # Not picked up by the pytest or Kotlin lanes.
    assert _completed_functional_subtasks(plan) == []
    assert _completed_kotlin_subtasks(plan) == []


def test_java_runner_fails_closed_when_sandbox_unconfigured(
    tmp_path: Path, monkeypatch
) -> None:
    """An unconfigured sandbox must be visible in the signal, not silently green."""
    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", lambda *a, **k: None)
    runner = _resolve_java_runner_fn(tmp_path / "spec", tmp_path / "proj")
    res = runner(tmp_path / "spec" / "X.java", tmp_path / "proj", 0)
    assert res.returncode == 1
    assert "java nix lane unavailable" in res.stderr


def test_build_all_bundles_runs_java_through_the_maven_lane(
    tmp_path: Path, monkeypatch
) -> None:
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    rel = "src/test/java/CalcTest.java"
    src = spec_dir / rel
    src.parent.mkdir(parents=True)
    src.write_text("class CalcTest {}\n")

    calls: list[tuple[Path, Path | None]] = []

    def _fake(spec, proj, *, hint=None, **k):
        calls.append((Path(proj), hint))
        return DockerRunResult(returncode=0, stdout="BUILD SUCCESS", argv=["mvn"])

    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", _fake)
    java = _completed_java_subtasks(_plan(_java_subtask()))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], (), java)

    assert (project_dir / rel).is_file()  # staged at its repo path
    assert len(bundles) == 1 and bundles[0].test_id == "st-jv-0"
    assert calls and all(p == project_dir for p, _ in calls)
    assert calls[0][1] == Path(rel)


def test_java_never_goes_through_the_gradle_or_pytest_lane(
    tmp_path: Path, monkeypatch
) -> None:
    """The routing mutation guard: Java must use Maven, not Kotlin's runner.

    Pointing the Java partition at run_gradle_lane_via_nix would build Java with
    Gradle, which the fixture module has no build file for — a failure that would
    look like a broken lane rather than a mis-route.
    """
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    rel = "src/test/java/CalcTest.java"
    (spec_dir / rel).parent.mkdir(parents=True)
    (spec_dir / rel).write_text("class CalcTest {}\n")

    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda *a, **k: True)

    def _no(*a, **k):
        raise AssertionError("Java was sent to the wrong lane")

    monkeypatch.setattr("agents.evaluator.run_pytest_lane_via_nix", _no)
    monkeypatch.setattr("agents.nix_env.run_gradle_lane_via_nix", _no)

    maven_runs: list[int] = []

    def _fake(spec, proj, *, hint=None, **k):
        maven_runs.append(1)
        return DockerRunResult(returncode=0, stdout="ok", argv=["mvn"])

    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", _fake)
    st = _java_subtask()
    st["files_to_create"] = [rel]
    java = _completed_java_subtasks(_plan(st))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], (), java)
    assert len(bundles) == 1
    assert maven_runs, "the Maven lane must have run"
    assert bundles[0].stability is not None


def test_java_stability_is_computed_once_for_the_module(
    tmp_path: Path, monkeypatch
) -> None:
    """`mvn test` covers the module, so three subtasks share one stability pass."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    subs = []
    for i in range(3):
        st = _java_subtask(f"st-jv-{i}")
        st["files_to_create"] = [f"src/test/java/T{i}Test.java"]
        (spec_dir / st["files_to_create"][0]).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / st["files_to_create"][0]).write_text("class T {}\n")
        subs.append(st)

    runs: list[int] = []

    def _fake(spec, proj, *, hint=None, **k):
        runs.append(1)
        return DockerRunResult(returncode=0, stdout="ok", argv=["mvn"])

    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", _fake)
    java = _completed_java_subtasks(_plan(*subs))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], (), java)
    assert len(bundles) == 3
    assert len(runs) == 3, (
        "one module-wide stability pass (3 reruns), not 3 per subtask"
    )
