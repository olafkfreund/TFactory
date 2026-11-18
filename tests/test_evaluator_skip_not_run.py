"""All-skipped pytest output must never become evidence of a pass."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import evaluator
from agents.mutate_probe import MutationApplied, MutationVerdict
from tools.runners.docker_runner import DockerRunResult


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("__PYTEST_EXIT=", 125),
        ("__JEST_EXIT=", 0),
        ("__PW_EXIT=", 0),
    ],
)
def test_parse_marker_exit_maps_only_pytest(prefix, expected):
    assert (
        evaluator._parse_marker_exit(f"1 skipped in 0.01s\n{prefix}0", prefix, 1)
        == expected
    )
    assert evaluator._parse_marker_exit(f"1 passed\n{prefix}0", prefix, 1) == 0
    assert evaluator._parse_marker_exit(f"1 skipped\n{prefix}1", prefix, 0) == 1


def test_mutation_not_run_for_skipped_exit():
    mutation = MutationApplied(operator="replace", lineno=1, before="a", after="b")
    result = evaluator._mutation_from_codes([("mutant", mutation)], [125])
    assert result is not None and result.verdict == MutationVerdict.NOT_RUN


def test_host_pytest_runner_maps_all_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, "_ensure_host_venv", lambda _: tmp_path / "venv")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="1 skipped in 0.01s\n__PYTEST_EXIT=0\n", stderr=""
        ),
    )
    result = evaluator._run_pytest_on_host(tmp_path, Path("test_case.py"), {}, tmp_path)
    assert result.returncode == 125


def test_docker_pytest_runner_maps_all_skipped(tmp_path, monkeypatch):
    spec = tmp_path / "spec"
    (spec / "tests").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    test_file = spec / "tests" / "test_case.py"
    test_file.write_text("def test_case(): pass\n")
    monkeypatch.setattr(evaluator, "_nix_verify_mode", lambda *a, **k: False)
    monkeypatch.setattr(evaluator, "_host_runner_mode", lambda *a, **k: False)
    monkeypatch.setattr(evaluator, "_stage_sut_into_scratch", lambda *a, **k: None)

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, **kwargs):
            return DockerRunResult(
                returncode=0,
                stdout="1 skipped in 0.01s\n__PYTEST_EXIT=0\n",
                stderr="",
                argv=["pytest"],
            )

    monkeypatch.setattr("tools.runners.docker_runner.DockerRunner", FakeRunner)
    result = evaluator._resolve_runner_fn(spec, project)(test_file, project, 0)
    assert result.returncode == 125
