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


class _FakeJobResult:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class _FakeSandbox:
    """A stand-in ExecutionSandbox: records calls and returns canned Job stdout."""

    def __init__(self, stdout: str) -> None:
        self._stdout = stdout
        self.calls: list[tuple] = []

    def run(self, commands, *, workdir=None, timeout=900):  # noqa: ANN001, ANN002
        self.calls.append((commands, workdir, timeout))
        return _FakeJobResult(self._stdout)


def _deploy_stdout(
    *,
    tf_init: int = 0,
    tf_validate: int = 0,
    tf_plan: int = 0,
    tfsec: int = 0,
    trivy: int = 0,
) -> str:
    """Job stdout for the five runnable deploy steps of a ``main.tf`` fixture, in
    ``plan_deploy_steps`` order: terraform-init(0), terraform-validate(1),
    terraform-plan(2), tfsec(3), trivy(4) — each bracketed by exit markers."""
    exits = [tf_init, tf_validate, tf_plan, tfsec, trivy]
    lines: list[str] = []
    for i, code in enumerate(exits):
        lines += [
            f"__DEPLOY_STEP_{i}_BEGIN",
            f"step {i}",
            f"__DEPLOY_STEP_{i}_EXIT={code}",
        ]
    return "\n".join(lines)


def test_run_deploy_lane_via_nix_runs_terraform_and_scanners(tmp_path: Path) -> None:
    """The Nix path runs the OpenTofu rung + tfsec/trivy in ONE Job, writes the
    flake to a dedicated subdir (never clobbering an app flake), and reaches VAL-2
    when terraform-plan passes (the dry-run ceiling); prowler stays not_run."""
    from agents.nix_env import run_deploy_lane_via_nix

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text('resource "null_resource" "x" {}')
    sandbox = _FakeSandbox(_deploy_stdout())

    result = run_deploy_lane_via_nix(project, files=["main.tf"], sandbox=sandbox)

    assert result is not None
    # terraform-plan (VAL-2) passed → achieved the dry-run ceiling.
    assert result.verification["achieved_level"] == "VAL-2"
    # The generated deploy flake lives in the dedicated subdir, not the worktree
    # root, so it can never overwrite an app-owned flake.nix.
    assert (project / ".tf_deploy" / "flake.nix").exists()
    assert not (project / "flake.nix").exists()
    by_name = {s.name: s.status for s in result.steps}
    assert by_name["terraform-init"] == "passed"
    assert by_name["terraform-validate"] == "passed"
    assert by_name["terraform-plan"] == "passed"
    assert by_name["tfsec"] == "passed"
    assert by_name["trivy"] == "passed"
    # prowler isn't in the deploy flake → honest not_run.
    assert by_name["cloud-prowler"] == "not_run"
    # Exactly one Job ran every step.
    assert len(sandbox.calls) == 1


def test_run_deploy_lane_via_nix_no_sandbox_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    """Absent the Nix sandbox the lane returns None so the caller falls back."""
    from agents.nix_env import run_deploy_lane_via_nix

    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text("")
    assert run_deploy_lane_via_nix(project, files=["main.tf"]) is None


def test_run_deploy_lane_via_nix_failed_scan_is_not_a_silent_pass(
    tmp_path: Path,
) -> None:
    """A scanner that exits non-zero in the Job is recorded as failed (the verdict
    can never overclaim past a real failure)."""
    from agents.nix_env import run_deploy_lane_via_nix

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text('resource "null_resource" "x" {}')
    # trivy (the strict gate) exits non-zero on a HIGH/CRITICAL misconfig.
    sandbox = _FakeSandbox(_deploy_stdout(trivy=1))

    result = run_deploy_lane_via_nix(project, files=["main.tf"], sandbox=sandbox)

    assert result is not None
    assert result.ok is False
    assert {s.name: s.status for s in result.steps}["trivy"] == "failed"


def test_maybe_run_deploy_lane_prefers_nix_when_configured(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end wire: a high-risk contract + a configured Nix sandbox routes the
    live (run_fn=None) path through the Nix Job and persists the real proof."""
    import agents.nix_env as nx

    spec_dir = tmp_path / "spec"
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text('resource "null_resource" "x" {}')
    _write_contract(spec_dir, {"risk_class": "high"})
    sandbox = _FakeSandbox(_deploy_stdout())
    monkeypatch.setattr(nx, "nix_runner_from_env", lambda: sandbox)

    result = maybe_run_deploy_lane(spec_dir, project)  # run_fn=None → live Nix path

    assert result is not None
    assert result["achieved_level"] == "VAL-2"
    proof = json.loads((spec_dir / _PROOF).read_text())
    assert proof["achieved_level"] == "VAL-2"
    assert len(sandbox.calls) == 1


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
