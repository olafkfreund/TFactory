"""Tests for DockerRunner — Task 4 (#5), sub-task 4.1.

Most tests stub ``subprocess.run`` so we never spin up a real container;
the integration smoke at the bottom of the file is skipped when ``docker``
isn't on PATH so the suite stays green on dev machines without Docker.

Covers:
  - argv has the lockdown flags: --network none, --read-only, ro repo, rw scratch, cpu/mem/pid limits
  - tmpfs /tmp is added when read_only_rootfs=True
  - extra env vars become -e args in order
  - extra_args are appended before the image
  - rejects empty command
  - rejects relative paths
  - binary is configurable (docker / podman / env override)
  - run() raises DockerRunnerError when binary missing
  - run() raises DockerTimeoutError on subprocess.TimeoutExpired
  - run() picks up junit + coverage from scratch when they exist
  - run_pytest convenience wrapper produces a sane bash -lc string
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.runners.docker_runner import (
    DockerRunner,
    DockerRunnerError,
    DockerTimeoutError,
)


# ── argv construction ────────────────────────────────────────────────────


def _basic_argv(**overrides):
    r = DockerRunner(**overrides)
    return r.build_argv(
        repo_path=Path("/tmp/repo"),
        scratch_path=Path("/tmp/scratch"),
        command=["pytest", "-v"],
    )


def test_argv_has_lockdown_flags():
    argv = _basic_argv()
    assert "--network" in argv and "none" in argv
    assert "--read-only" in argv
    assert "--cpus" in argv
    assert "--memory" in argv
    assert "--pids-limit" in argv


def test_argv_mounts():
    argv = _basic_argv()
    assert "/tmp/repo:/work:ro" in argv
    assert "/tmp/scratch:/scratch:rw" in argv


def test_argv_workdir_is_scratch():
    argv = _basic_argv()
    idx = argv.index("-w")
    assert argv[idx + 1] == "/scratch"


def test_argv_default_image_appears_before_command():
    argv = _basic_argv()
    image_idx = argv.index("tfactory-runner-python:latest")
    cmd_idx = argv.index("pytest")
    assert image_idx < cmd_idx


def test_argv_uses_default_image_constant():
    argv = _basic_argv()
    assert DockerRunner.DEFAULT_IMAGE in argv


def test_tmpfs_tmp_added_when_read_only_rootfs():
    argv = _basic_argv()
    assert "--tmpfs" in argv
    idx = argv.index("--tmpfs")
    assert argv[idx + 1].startswith("/tmp:")


def test_no_tmpfs_when_read_only_rootfs_disabled():
    argv = _basic_argv(read_only_rootfs=False)
    assert "--read-only" not in argv
    assert "--tmpfs" not in argv


def test_env_vars_become_e_flags():
    r = DockerRunner()
    argv = r.build_argv(
        repo_path=Path("/tmp/r"),
        scratch_path=Path("/tmp/s"),
        command=["cmd"],
        env={"FOO": "1", "BAR": "two"},
    )
    assert "FOO=1" in argv
    assert "BAR=two" in argv


def test_extra_args_appended_before_image():
    r = DockerRunner()
    argv = r.build_argv(
        repo_path=Path("/tmp/r"),
        scratch_path=Path("/tmp/s"),
        command=["cmd"],
        extra_args=["--user", "1000"],
    )
    image_idx = argv.index("tfactory-runner-python:latest")
    user_idx = argv.index("--user")
    assert user_idx < image_idx


def test_custom_image_honoured():
    argv = _basic_argv(image="myreg/custom:dev")
    assert "myreg/custom:dev" in argv
    assert "tfactory-runner-python:latest" not in argv


def test_custom_binary_honoured():
    argv = _basic_argv(binary="podman")
    assert argv[0] == "podman"


def test_binary_env_override(monkeypatch):
    monkeypatch.setenv("TFACTORY_CONTAINER_BIN", "podman")
    argv = _basic_argv()
    assert argv[0] == "podman"


# ── validation ───────────────────────────────────────────────────────────


def test_empty_command_rejected():
    r = DockerRunner()
    with pytest.raises(DockerRunnerError):
        r.build_argv(
            repo_path=Path("/tmp/r"),
            scratch_path=Path("/tmp/s"),
            command=[],
        )


def test_relative_repo_path_rejected():
    r = DockerRunner()
    with pytest.raises(DockerRunnerError):
        r.build_argv(
            repo_path=Path("relative/repo"),
            scratch_path=Path("/tmp/s"),
            command=["cmd"],
        )


def test_relative_scratch_path_rejected():
    r = DockerRunner()
    with pytest.raises(DockerRunnerError):
        r.build_argv(
            repo_path=Path("/tmp/r"),
            scratch_path=Path("relative/scratch"),
            command=["cmd"],
        )


# ── execution (mocked subprocess) ────────────────────────────────────────


def _fake_completed(returncode=0, stdout="ok", stderr=""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_run_raises_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    r = DockerRunner()
    with pytest.raises(DockerRunnerError, match="not found on PATH"):
        r.run(
            repo_path=tmp_path,
            scratch_path=tmp_path,
            command=["cmd"],
        )


def test_run_returns_result_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(0, "hello", "")
    )
    r = DockerRunner()
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["echo", "hi"],
    )
    assert res.ok is True
    assert res.returncode == 0
    assert res.stdout == "hello"
    # argv recorded for debug
    assert res.argv and res.argv[0] == "docker"


def test_run_raises_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)
    monkeypatch.setattr(subprocess, "run", _boom)
    r = DockerRunner()
    with pytest.raises(DockerTimeoutError):
        r.run(
            repo_path=tmp_path,
            scratch_path=tmp_path,
            command=["echo"],
            timeout_sec=1,
        )


def test_run_picks_up_junit_and_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(0))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "junit.xml").write_text("<testsuites/>")
    (scratch / "coverage.xml").write_text("<coverage/>")

    r = DockerRunner()
    res = r.run(
        repo_path=tmp_path,
        scratch_path=scratch,
        command=["pytest"],
    )
    assert res.junit_xml_path == scratch / "junit.xml"
    assert res.coverage_xml_path == scratch / "coverage.xml"


def test_run_omits_artifact_paths_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(0))
    r = DockerRunner()
    res = r.run(repo_path=tmp_path, scratch_path=tmp_path, command=["echo"])
    assert res.junit_xml_path is None
    assert res.coverage_xml_path is None


# ── run_pytest convenience wrapper ──────────────────────────────────────


def test_run_pytest_command_shape(monkeypatch, tmp_path):
    """The wrapper builds bash -lc with pytest + cov + junit emit."""
    captured = {}
    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run_pytest(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        tests_relpath="tests/functional",
        cov_package="app",
    )
    argv = captured["argv"]
    # The final command is in the last 3 entries: bash -lc "..."
    assert argv[-3:][:2] == ["bash", "-lc"]
    cmd_str = argv[-1]
    assert "pytest tests/functional" in cmd_str
    assert "--cov=app" in cmd_str
    assert "--junitxml=/scratch/junit.xml" in cmd_str
    assert "--cov-report=xml:/scratch/coverage.xml" in cmd_str


def test_run_pytest_omits_cov_arg_when_package_unset(monkeypatch, tmp_path):
    captured = {}
    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run_pytest(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        tests_relpath="tests/",
    )
    assert "--cov=" not in captured["argv"][-1]


# ── real-docker integration smoke (skipped if docker absent) ────────────


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "--version"],
            capture_output=True, check=True, timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_docker_image_runs_echo(tmp_path):
    """Smoke: the runner can fire a docker invocation and capture stdout.

    Uses busybox (3MB, ships everywhere) — does NOT require the
    tfactory-runner-python image to be built. Validates the wiring
    end-to-end without the build artifact.
    """
    r = DockerRunner(image="busybox:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["echo", "tfactory-smoke"],
        timeout_sec=30,
    )
    assert res.ok, res.stderr
    assert "tfactory-smoke" in res.stdout
