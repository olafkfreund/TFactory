"""Tests for the live deploy-lane dispatch (#597).

``agents.deploy_lane.maybe_run_deploy_lane`` is the missing wire between the
RFC-0013 deploy executor and the triager's deploy-gate: when a spec's contract
marks the change high-risk/production it runs the DRY-RUN deploy lane and persists
``findings/deploy_verification.json``. These tests inject a fake step runner so
they exercise the full dispatch without any real terraform/helm installed.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.deploy_lane import _discover_deploy_files, maybe_run_deploy_lane
from tools.runners.deploy_runner import StepResult


def _write_contract(spec_dir: Path, deployment: dict | None) -> None:
    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    # read_task_contract only accepts a doc with contract_version / a tfactory
    # block (RFC-0002); real contracts always carry it.
    contract: dict = {"contract_version": "2"}
    if deployment is not None:
        contract["deployment"] = deployment
    (ctx / "task_contract.json").write_text(json.dumps(contract))


def _ok(argv: tuple[str, ...]) -> StepResult:
    """A fake step runner that reports every step as passed."""
    return StepResult(name=argv[0], level="VAL-0", status="passed", returncode=0)


_PROOF = "findings/deploy_verification.json"


def test_low_risk_is_noop(tmp_path: Path) -> None:
    """A non-high-risk / non-production contract dispatches nothing."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "main.tf").write_text('resource "null_resource" "x" {}')
    _write_contract(
        spec_dir, {"risk_class": "low", "production_classification": "internal"}
    )

    result = maybe_run_deploy_lane(
        spec_dir, project_dir, run_fn=_ok, tool_available=lambda _t: True
    )

    assert result is None
    assert not (spec_dir / _PROOF).exists()


def test_absent_deployment_block_is_noop(tmp_path: Path) -> None:
    """No deployment block at all → not required → no dispatch."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_contract(spec_dir, None)

    assert maybe_run_deploy_lane(spec_dir, project_dir, run_fn=_ok) is None
    assert not (spec_dir / _PROOF).exists()


def test_high_risk_with_tools_writes_dry_run_proof(tmp_path: Path) -> None:
    """High-risk contract + IaC files + present tools → persisted dry-run proof."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "main.tf").write_text('resource "null_resource" "x" {}')
    _write_contract(spec_dir, {"risk_class": "high"})

    result = maybe_run_deploy_lane(
        spec_dir, project_dir, run_fn=_ok, tool_available=lambda _t: True
    )

    assert result is not None
    # terraform validate (VAL-0) + plan (VAL-2) both pass → achieved VAL-2.
    assert result["achieved_level"] == "VAL-2"
    # The proof the triager's deploy-gate reads is on disk and matches.
    proof = json.loads((spec_dir / _PROOF).read_text())
    assert proof["achieved_level"] == "VAL-2"


def test_production_classification_forces_dispatch(tmp_path: Path) -> None:
    """production_classification=production requires the lane even at low risk."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "main.tf").write_text('resource "null_resource" "x" {}')
    _write_contract(spec_dir, {"production_classification": "production"})

    result = maybe_run_deploy_lane(
        spec_dir, project_dir, run_fn=_ok, tool_available=lambda _t: True
    )

    assert result is not None
    assert (spec_dir / _PROOF).exists()


def test_high_risk_tools_absent_is_honest_not_run(tmp_path: Path) -> None:
    """When the runner lacks the tools, the proof records an honest VAL-0 (never a
    silent pass) — so the gate keeps holding rather than overclaiming."""
    spec_dir = tmp_path / "spec"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "main.tf").write_text('resource "null_resource" "x" {}')
    _write_contract(spec_dir, {"risk_class": "high"})

    result = maybe_run_deploy_lane(
        spec_dir, project_dir, tool_available=lambda _t: False
    )

    assert result is not None
    assert result["achieved_level"] == "VAL-0"
    assert (spec_dir / _PROOF).exists()


def test_discover_deploy_files_matches_iac(tmp_path: Path) -> None:
    """Discovery finds terraform, helm, and k8s manifests; ignores plain code."""
    project = tmp_path / "p"
    (project / "k8s").mkdir(parents=True)
    (project / "chart").mkdir(parents=True)
    (project / "main.tf").write_text("")
    (project / "chart" / "Chart.yaml").write_text("")
    (project / "k8s" / "deploy.yaml").write_text("")
    (project / "app.py").write_text("print('hi')")

    found = set(_discover_deploy_files(project))

    assert "main.tf" in found
    assert "chart/Chart.yaml" in found
    assert "k8s/deploy.yaml" in found
    assert "app.py" not in found
