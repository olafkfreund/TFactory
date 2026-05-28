"""Tests for the lane → runner dispatcher — Task 4 (#5)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.runners.docker_runner import DockerRunner
from tools.runners.lane_dispatch import (
    DispatchResult,
    LaneNotImplementedError,
    dispatch_lane,
    is_lane_lit,
)


# ── lit / not-lit gate ──────────────────────────────────────────────────


def test_only_functional_is_lit_at_mvp():
    assert is_lane_lit("functional") is True
    for lane in ("sast", "deps", "secrets", "dast", "fuzz", "mutation"):
        assert is_lane_lit(lane) is False, f"{lane} should not be lit"


# ── gated lanes raise with phase context ─────────────────────────────────


@pytest.mark.parametrize("lane,phase_token", [
    ("sast", "phase 3"),
    ("deps", "phase 3"),
    ("secrets", "phase 3"),
    ("mutation", "phase 2"),
    ("dast", "phase 5"),
    ("fuzz", "phase 5"),
])
def test_gated_lane_raises_with_phase_token(lane, phase_token):
    with pytest.raises(LaneNotImplementedError, match=phase_token):
        dispatch_lane(lane=lane)


def test_unknown_lane_raises_lane_not_implemented():
    with pytest.raises(LaneNotImplementedError, match="unknown lane"):
        dispatch_lane(lane="telepathy")


# ── functional lane requires DockerRunner ────────────────────────────────


def test_functional_without_runner_raises_value_error():
    with pytest.raises(ValueError, match="docker_runner"):
        dispatch_lane(lane="functional")


def test_functional_without_kwargs_raises_value_error():
    r = DockerRunner()
    with pytest.raises(ValueError, match="docker_run_kwargs"):
        dispatch_lane(lane="functional", docker_runner=r)


# ── functional lane routes to docker_runner.run ─────────────────────────


def test_functional_invokes_docker_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")

    captured: dict = {}
    def _fake_run(*args, **kwargs):
        captured["argv"] = args[0]
        cp = MagicMock(spec=subprocess.CompletedProcess)
        cp.returncode = 0
        cp.stdout = "out"
        cp.stderr = ""
        return cp

    monkeypatch.setattr(subprocess, "run", _fake_run)

    r = DockerRunner(image="tfactory-runner-python:latest")
    result = dispatch_lane(
        lane="functional",
        docker_runner=r,
        docker_run_kwargs={
            "repo_path": tmp_path,
            "scratch_path": tmp_path,
            "command": ["pytest"],
        },
    )

    assert isinstance(result, DispatchResult)
    assert result.lane == "functional"
    assert result.runner_used == "docker"
    assert result.docker_result is not None
    assert result.docker_result.returncode == 0
    # The docker invocation actually fired
    assert "docker" in captured["argv"][0]
    assert "tfactory-runner-python:latest" in captured["argv"]
