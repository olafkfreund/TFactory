"""Tests for the lane → runner dispatcher — Task 4 (#5),
restructured for v0.2 Task 0 (#16).

v0.2 lights ALL FIVE modality lanes (unit/browser/api/integration/mutation)
via the same DockerRunner interface — per-framework runner image is
supplied by the caller via docker_run_kwargs. Browser + Integration
additionally need AppRuntime (Task 8) but that wires into the Executor,
not the dispatcher.
"""

from __future__ import annotations

import shutil
import subprocess
import warnings
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


# ── lit / not-lit gate (v0.2 spine) ────────────────────────────────────


def test_all_five_v02_lanes_are_lit():
    for lane in ("unit", "browser", "api", "integration", "mutation"):
        assert is_lane_lit(lane) is True, f"{lane} should be lit in v0.2"


def test_v01_aliases_are_still_lit_for_compatibility():
    """v0.1 lane names accepted through v0.2 with deprecation warning."""
    for legacy_lane in ("functional", "sast", "dast", "fuzz"):
        assert is_lane_lit(legacy_lane) is True, (
            f"{legacy_lane} should be lit via v0.1 alias compatibility"
        )


def test_out_of_scope_security_lanes_are_not_lit_directly():
    """Security lanes that don't have a v0.1 alias remain out of scope."""
    for lane in ("deps", "secrets"):
        assert is_lane_lit(lane) is False, f"{lane} should not be lit"


# ── out-of-scope lanes raise with "out of scope" message ────────────────


@pytest.mark.parametrize("lane", ["deps", "secrets"])
def test_out_of_scope_lane_raises_with_decision_context(lane):
    """Security lanes from v0.1 spec without an alias raise structured error."""
    with pytest.raises(LaneNotImplementedError, match="out of scope"):
        dispatch_lane(lane=lane)


def test_unknown_lane_raises_lane_not_implemented():
    with pytest.raises(LaneNotImplementedError, match="unknown lane"):
        dispatch_lane(lane="telepathy")


# ── v0.1 alias dispatch path ────────────────────────────────────────────


@pytest.mark.parametrize("legacy_lane", ["functional", "sast", "dast", "fuzz"])
def test_v01_alias_dispatch_emits_deprecation_warning(
    legacy_lane, monkeypatch, tmp_path,
):
    """Calling dispatch_lane with a v0.1 name remaps to 'unit' + warns."""
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    def _fake_run(*args, **kwargs):
        cp = MagicMock(spec=subprocess.CompletedProcess)
        cp.returncode = 0
        cp.stdout = ""
        cp.stderr = ""
        return cp
    monkeypatch.setattr(subprocess, "run", _fake_run)

    r = DockerRunner(image="tfactory-runner-python:latest")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = dispatch_lane(
            lane=legacy_lane,
            docker_runner=r,
            docker_run_kwargs={
                "repo_path": tmp_path,
                "scratch_path": tmp_path,
                "command": ["pytest"],
            },
        )
    assert result.lane == "unit"  # remapped
    assert any(
        issubclass(w.category, DeprecationWarning) for w in caught
    ), f"no DeprecationWarning emitted for legacy lane {legacy_lane!r}"


# ── lit lanes require DockerRunner ──────────────────────────────────────


@pytest.mark.parametrize("lane", ["unit", "browser", "api", "integration", "mutation"])
def test_lit_lane_without_runner_raises_value_error(lane):
    with pytest.raises(ValueError, match="docker_runner"):
        dispatch_lane(lane=lane)


def test_lit_lane_without_kwargs_raises_value_error():
    r = DockerRunner()
    with pytest.raises(ValueError, match="docker_run_kwargs"):
        dispatch_lane(lane="unit", docker_runner=r)


# ── lit lanes route to docker_runner.run ────────────────────────────────


@pytest.mark.parametrize("lane", ["unit", "browser", "api", "integration", "mutation"])
def test_lit_lane_invokes_docker_runner(lane, monkeypatch, tmp_path):
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

    r = DockerRunner(image="tfactory-runner-pytest:latest")
    result = dispatch_lane(
        lane=lane,
        docker_runner=r,
        docker_run_kwargs={
            "repo_path": tmp_path,
            "scratch_path": tmp_path,
            "command": ["pytest"],
        },
    )

    assert isinstance(result, DispatchResult)
    assert result.lane == lane
    assert result.runner_used == "docker"
    assert result.docker_result is not None
    assert result.docker_result.returncode == 0
    # The docker invocation actually fired
    assert "docker" in captured["argv"][0]
    assert "tfactory-runner-pytest:latest" in captured["argv"]
