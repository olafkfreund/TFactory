"""#1407: a Nix batched pytest lane keeps junit + coverage, packed and unpacked.

Drives the real ``run_pytest_lane_via_nix`` and ``_nix_batched_signals`` with a
fake sandbox. Unpacked, the Job writes into the co-mounted scratch. Packed, the
fake follows ``KubeJobSandbox._pack_lane`` / ``_collect_lane``: tar the workdir,
extract into the pod's ``/work``, run, tar ``/work`` back and extract into the
workdir.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

_STDOUT = (
    "__PYTEST_RUN=1\n.\n__PYTEST_EXIT=0\n"
    "__PYTEST_RUN=2\n.\n__PYTEST_EXIT=0\n"
    "__PYTEST_RUN=3\n.\n__PYTEST_EXIT=0\n"
)
_COVERAGE = (
    '<?xml version="1.0" ?>\n<coverage line-rate="1"><packages><package><classes>'
    '<class filename="src/app/ordinal.py"><lines>'
    '<line number="1" hits="1"/><line number="2" hits="1"/>'
    "</lines></class></classes></package></packages></coverage>\n"
)
_JUNIT = '<?xml version="1.0" ?>\n<testsuites><testsuite tests="1"/></testsuites>\n'


def _job_writes_reports(work: Path) -> None:
    stage = (
        work / ".tf_pytest"
    )  # nix_env._PYTEST_STAGE, where --junitxml/--cov-report point
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "junit.xml").write_text(_JUNIT)
    (stage / "coverage.xml").write_text(_COVERAGE)


class _UnpackedSandbox:
    """The Job sees the scratch via the PVC co-mount: it writes straight into it."""

    def __init__(self, write: bool = True) -> None:
        self.write = write

    def run(self, commands, workdir=None, timeout=0):
        if self.write:
            _job_writes_reports(Path(workdir))
        return SimpleNamespace(returncode=0, stdout=_STDOUT)


class _PackedSandbox:
    """The Job works on an emptyDir; the workdir only changes via the pushed-back tar."""

    def __init__(self, pod_root: Path) -> None:
        self.pod_root = pod_root

    def run(self, commands, workdir=None, timeout=0):
        from tools.runners.artifact_store import _safe_extract, _tar_workspace

        pod_work = self.pod_root / "work"
        _safe_extract(_tar_workspace(Path(workdir)), pod_work)  # pack + in-pod untar
        _job_writes_reports(pod_work)
        _safe_extract(_tar_workspace(pod_work), Path(workdir))  # push-back + collect
        return SimpleNamespace(returncode=0, stdout=_STDOUT)


def _setup(tmp_path, monkeypatch, sandbox):
    from agents import nix_env

    monkeypatch.setattr(nix_env, "_JUNIT_ARTIFACTS_ROOT", tmp_path / "nixjunit")
    monkeypatch.setattr(nix_env, "environment_from_contract", lambda *_: {"x": 1})
    monkeypatch.setattr(nix_env, "is_nix_environment", lambda *_: True)
    monkeypatch.setattr(
        nix_env, "materialize_flake", lambda *a, **k: SimpleNamespace(generated=False)
    )
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: sandbox)
    # The real lane, not a stub: the evaluator imports it by name.
    monkeypatch.setattr(
        "agents.evaluator.run_pytest_lane_via_nix", nix_env.run_pytest_lane_via_nix
    )

    project = tmp_path / "proj"
    (project / "src" / "app").mkdir(parents=True)
    (project / "src" / "app" / "ordinal.py").write_text("def f():\n    return 1\n")
    spec_dir = tmp_path / "spec"
    (spec_dir / "tests").mkdir(parents=True)
    (spec_dir / "tests" / "test_x.py").write_text("def test_x():\n    assert 1 == 1\n")
    subtask = {
        "id": "t-1",
        "language": "python",
        "files_to_create": ["tests/test_x.py"],
    }
    return spec_dir, project, subtask


@pytest.mark.parametrize("mode", ["unpacked", "packed"])
def test_batched_lane_persists_junit_and_coverage(tmp_path, monkeypatch, mode):
    from agents import evaluator

    sandbox = (
        _UnpackedSandbox() if mode == "unpacked" else _PackedSandbox(tmp_path / "pod")
    )
    spec_dir, project, subtask = _setup(tmp_path, monkeypatch, sandbox)

    stability, _mutation = evaluator._nix_batched_signals(spec_dir, project, subtask)

    assert stability is not None
    art = spec_dir / "findings" / "_run_artifacts" / "test_x"
    assert (art / "junit.xml").read_text() == _JUNIT
    assert (art / "coverage.xml").read_text() == _COVERAGE
    covered, _delta = evaluator._measured_coverage(spec_dir, "t-1", "test_x")
    assert covered == 2


def test_batched_stability_persists_coverage(tmp_path, monkeypatch):
    from agents import evaluator

    spec_dir, project, _ = _setup(
        tmp_path, monkeypatch, _PackedSandbox(tmp_path / "pod")
    )

    assert evaluator._nix_batched_stability(
        spec_dir, project, spec_dir / "tests" / "test_x.py"
    )
    assert evaluator._measured_coverage(spec_dir, "t-1", "test_x")[0] == 2


def test_no_reports_stays_unmeasured(tmp_path, monkeypatch):
    from agents import evaluator

    spec_dir, project, subtask = _setup(
        tmp_path, monkeypatch, _UnpackedSandbox(write=False)
    )

    stability, _ = evaluator._nix_batched_signals(spec_dir, project, subtask)

    assert stability is not None
    assert not (spec_dir / "findings" / "_run_artifacts" / "test_x").exists()
    assert evaluator._measured_coverage(spec_dir, "t-1", "test_x") == (None, None)
