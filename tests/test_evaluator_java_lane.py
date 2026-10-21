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


# --- Gradle-built Java routing (#3151) --------------------------------------


def _route(tmp_path: Path, monkeypatch, files: list[str], gradle_result=None):
    """Run the Java runner over a tmp project; return (calls, result)."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    for f in files:
        (project_dir / f).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / f).write_text("")
    calls: dict[str, dict] = {}

    def _rec(name, result):
        def _fn(spec, proj, *, hint=None, **k):
            calls[name] = k
            return result

        return _fn

    ok = DockerRunResult(returncode=0, stdout="ok", argv=[])
    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", _rec("maven", ok))
    monkeypatch.setattr(
        "agents.nix_env.run_gradle_lane_via_nix", _rec("gradle", gradle_result or ok)
    )
    runner = _resolve_java_runner_fn(spec_dir, project_dir)
    return calls, runner(spec_dir / "CalcTest.java", project_dir, 0)


def test_java_pom_only_routes_to_maven(tmp_path: Path, monkeypatch) -> None:
    calls, _ = _route(tmp_path, monkeypatch, ["pom.xml"])
    assert list(calls) == ["maven"]


def test_java_gradle_only_routes_to_gradle_with_java_env(
    tmp_path: Path, monkeypatch
) -> None:
    calls, _ = _route(tmp_path, monkeypatch, ["settings.gradle", "build.gradle"])
    assert list(calls) == ["gradle"]
    env = calls["gradle"]["env"]
    assert env["language"] == "java" and "gradle" in env["system_packages"]


def test_java_pom_plus_gradle_routes_to_maven(tmp_path: Path, monkeypatch) -> None:
    calls, _ = _route(tmp_path, monkeypatch, ["pom.xml", "build.gradle"])
    assert list(calls) == ["maven"]


def test_java_no_build_files_routes_to_maven(tmp_path: Path, monkeypatch) -> None:
    calls, _ = _route(tmp_path, monkeypatch, [])
    assert list(calls) == ["maven"]


def test_java_gradle_fails_closed_naming_gradle(tmp_path: Path, monkeypatch) -> None:
    ok = DockerRunResult(returncode=0, stdout="", argv=[])
    spec_dir, proj = tmp_path / "spec", tmp_path / "proj"
    proj.mkdir()
    (proj / "build.gradle").write_text("")
    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", lambda *a, **k: ok)
    monkeypatch.setattr("agents.nix_env.run_gradle_lane_via_nix", lambda *a, **k: None)
    res = _resolve_java_runner_fn(spec_dir, proj)(spec_dir / "X.java", proj, 0)
    assert res.returncode == 1 and "gradle" in res.argv


def test_java_nested_pom_beside_root_gradle_routes_to_maven(
    tmp_path: Path, monkeypatch
) -> None:
    """ "No pom.xml anywhere" means anywhere, not just the root."""
    calls, _ = _route(tmp_path, monkeypatch, ["build.gradle", "sub/pom.xml"])
    assert list(calls) == ["maven"]


# --- Per-class verdicts and per-module grouping (#1341, #1356) ---------------

_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
<testsuite name="com.x.ATest" tests="3" failures="2" errors="0">
  <testcase name="one" classname="com.x.ATest"><failure message="boom"/></testcase>
  <testcase name="two -> three" classname="com.x.ATest">
    <failure message="nope">AssertionFailedError</failure>
  </testcase>
  <testcase name="ok" classname="com.x.ATest" time="0.1"/>
</testsuite>
<testsuite name="com.x.BTest" tests="2" failures="0" errors="0">
  <testcase name="b1" classname="com.x.BTest"/>
  <testcase name="b2" classname="com.x.BTest$Nested"></testcase>
</testsuite>
</testsuites>
"""


def _java_bundles(tmp_path: Path, monkeypatch, stems: list[str], report: str = _REPORT):
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    subs = []
    for stem in stems:
        st = _java_subtask(f"st-{stem}")
        st["files_to_create"] = [f"src/test/java/com/x/{stem}.java"]
        (spec_dir / st["files_to_create"][0]).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / st["files_to_create"][0]).write_text(f"class {stem} {{}}\n")
        subs.append(st)
    junit = tmp_path / "junit.xml"
    junit.write_text(report)

    def _fake(spec, proj, *, hint=None, **k):
        return DockerRunResult(
            returncode=1, stdout="BUILD FAILURE", junit_xml_path=junit, argv=["mvn"]
        )

    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", _fake)
    java = _completed_java_subtasks(_plan(*subs))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], (), java)
    return {b.test_id: b.stability for b in bundles}


def test_failures_in_one_class_leave_another_class_passing(
    tmp_path: Path, monkeypatch
) -> None:
    """#1341: two failures in ATest must not reject BTest."""
    from agents.stability_runner import StabilityVerdict

    got = _java_bundles(tmp_path, monkeypatch, ["ATest", "BTest"])
    assert got["st-BTest"].verdict == StabilityVerdict.STABLE
    assert got["st-ATest"].verdict == StabilityVerdict.CONSISTENT_FAIL
    assert "this test failed" in got["st-ATest"].runs[0].stdout_tail


def test_class_missing_from_report_is_error_not_consistent_fail(
    tmp_path: Path, monkeypatch
) -> None:
    from agents.stability_runner import StabilityVerdict

    got = _java_bundles(tmp_path, monkeypatch, ["CTest"])
    assert got["st-CTest"].verdict == StabilityVerdict.ERROR
    assert "did not run" in got["st-CTest"].error_message


def test_compile_failure_with_empty_report_stays_consistent_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """The lanes always write junit.xml; on a compile failure it has no testcases."""
    from agents.stability_runner import StabilityVerdict

    got = _java_bundles(
        tmp_path, monkeypatch, ["ATest", "BTest"], "<testsuites>\n</testsuites>\n"
    )
    assert got["st-ATest"].verdict == StabilityVerdict.CONSISTENT_FAIL
    assert got["st-BTest"].verdict == StabilityVerdict.CONSISTENT_FAIL


def test_each_maven_module_runs_its_own_stability_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """#1356: tests staged in module b must run in module b, not inherit a's."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    subs = []
    for mod in ("a", "b"):
        (project_dir / mod).mkdir(parents=True)
        (project_dir / mod / "pom.xml").write_text("<project/>")
        st = _java_subtask(f"st-{mod}")
        st["files_to_create"] = [f"{mod}/src/test/java/{mod.upper()}Test.java"]
        (spec_dir / st["files_to_create"][0]).parent.mkdir(parents=True)
        (spec_dir / st["files_to_create"][0]).write_text("class T {}\n")
        subs.append(st)

    hints: list[Path | None] = []

    def _fake(spec, proj, *, hint=None, **k):
        hints.append(hint)
        return DockerRunResult(returncode=0, stdout="ok", argv=["mvn"])

    monkeypatch.setattr("agents.nix_env.run_maven_lane_via_nix", _fake)
    java = _completed_java_subtasks(_plan(*subs))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], (), java)
    assert len(bundles) == 2
    assert len(hints) == 6
    assert {Path(h).parts[0] for h in hints} == {"a", "b"}
