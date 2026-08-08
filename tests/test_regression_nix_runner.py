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


# ── the coverage report is not dropped (#865) ───────────────────────────
def _cobertura(path: Path, covered: int, total: int) -> Path:
    lines = "".join(
        f'<line number="{n}" hits="{1 if n <= covered else 0}"/>'
        for n in range(1, total + 1)
    )
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{covered / total}">'
        f'<packages><package><classes><class filename="app.py" name="app">'
        f"<lines>{lines}</lines></class></classes></package></packages></coverage>",
        encoding="utf-8",
    )
    return path


def test_outcome_carries_the_coverage_report(tmp_path):
    """The Job writes a coverage.xml and the runner used to throw it away.

    `run_pytest_lane_via_nix` copies the report off the PVC scratch precisely so
    the caller can read it; the regression lane was the only consumer ignoring
    it, which left the coverage ledger permanently empty (#865).
    """
    xml = _cobertura(tmp_path / "coverage.xml", covered=17, total=20)
    out = outcome_from_run_result(
        _entry(), DockerRunResult(returncode=0, coverage_xml_path=xml)
    )
    assert out.coverage_report_path == str(xml)
    # The per-test float is this test's OWN project-wide reach — honest as such.
    assert out.coverage_pct == 85.0


def test_outcome_without_a_report_claims_no_coverage(tmp_path):
    out = outcome_from_run_result(_entry(), DockerRunResult(returncode=0))
    assert out.coverage_pct is None
    assert out.coverage_report_path is None


def test_unreadable_report_is_none_not_zero(tmp_path):
    """A corrupt report must not become 0.0% — that is a figure, and false."""
    bad = tmp_path / "coverage.xml"
    bad.write_text("not xml at all", encoding="utf-8")
    out = outcome_from_run_result(
        _entry(), DockerRunResult(returncode=0, coverage_xml_path=bad)
    )
    assert out.coverage_pct is None


def test_report_path_is_not_persisted(tmp_path):
    """It points at a per-run temp dir; a stored path that no longer resolves
    would be worse than no path at all."""
    xml = _cobertura(tmp_path / "coverage.xml", covered=1, total=2)
    out = outcome_from_run_result(
        _entry(), DockerRunResult(returncode=0, coverage_xml_path=xml)
    )
    assert "coverage_report_path" not in out.to_dict()
    assert out.to_dict()["coverage_pct"] == 50.0


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
