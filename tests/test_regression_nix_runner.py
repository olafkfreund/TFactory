"""Tests for the Nix-Job regression runner — RFC-0018 #484 (part 3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    NixJobRunner,
    NixSubstrateUnavailableError,
    RegressionRunner,
    TestStatus,
    UnsupportedFrameworkError,
    outcome_from_run_result,
)
from agents.regression import nix_runner as nr  # noqa: E402
from tools.runners.docker_runner import DockerRunResult  # noqa: E402


def _entry(test_id="t", framework="pytest", lane="unit") -> CorpusEntry:
    return CorpusEntry(
        test_id=test_id,
        test_file=f"tests/{test_id}.py",
        framework=framework,
        lane=lane,
        language="python",
    )


# ── pure mapping ─────────────────────────────────────────────────────
def test_outcome_from_result_pass_and_fail():
    e = _entry()
    assert (
        outcome_from_run_result(e, DockerRunResult(returncode=0)).status
        is TestStatus.PASSED
    )
    fail = outcome_from_run_result(e, DockerRunResult(returncode=1))
    assert fail.status is TestStatus.FAILED
    assert fail.test_id == "t" and fail.lane == "unit" and fail.framework == "pytest"


# ── runner conforms to protocol ─────────────────────────────────────────
def test_nix_runner_is_a_regression_runner(tmp_path):
    runner = NixJobRunner(spec_dir=tmp_path, project_dir=tmp_path)
    assert isinstance(runner, RegressionRunner)


# ── delegates to the Nix lane + maps result ─────────────────────────────
def test_run_delegates_to_nix_and_maps(monkeypatch, tmp_path):
    captured = {}

    def fake_nix(spec_dir, project_dir, test_file, *, extra_env=None, timeout=300):
        captured["test_file"] = Path(test_file)
        captured["extra_env"] = extra_env
        return DockerRunResult(returncode=0)

    monkeypatch.setattr(nr, "run_pytest_lane_via_nix", fake_nix)
    runner = NixJobRunner(
        spec_dir=tmp_path, project_dir=tmp_path, extra_env={"K": "V"}
    )
    out = runner.run(_entry("login"))
    assert out.status is TestStatus.PASSED
    # delegated with the worktree-relative test path resolved under project_dir
    assert captured["test_file"] == tmp_path / "tests" / "login.py"
    assert captured["extra_env"] == {"K": "V"}


def test_run_maps_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        nr, "run_pytest_lane_via_nix", lambda *a, **k: DockerRunResult(returncode=2)
    )
    out = NixJobRunner(spec_dir=tmp_path, project_dir=tmp_path).run(_entry())
    assert out.status is TestStatus.FAILED


# ── no silent fallback ─────────────────────────────────────────────────
def test_run_raises_when_substrate_unavailable(monkeypatch, tmp_path):
    # run_pytest_lane_via_nix returns None when no nix env / no runner image
    monkeypatch.setattr(nr, "run_pytest_lane_via_nix", lambda *a, **k: None)
    runner = NixJobRunner(spec_dir=tmp_path, project_dir=tmp_path)
    with pytest.raises(NixSubstrateUnavailableError):
        runner.run(_entry())


# ── unsupported framework is explicit (deferred slice) ───────────────────
def test_run_unsupported_framework(tmp_path):
    runner = NixJobRunner(spec_dir=tmp_path, project_dir=tmp_path)
    with pytest.raises(UnsupportedFrameworkError):
        runner.run(_entry(framework="playwright", lane="browser"))
