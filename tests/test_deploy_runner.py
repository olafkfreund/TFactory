"""Tests for the RFC-0013 DRY-RUN deploy lane (#446).

The deploy lane proves a change *would* ship via dry-run tooling only; it never
applies anything to a real environment. These tests inject a fake runner so they
exercise the full assembly + honest-VAL mapping without any real tool installed.
"""

from __future__ import annotations

import pytest
from tools.runners.deploy_runner import (
    ProductionApplyError,
    StepResult,
    assert_dry_run,
    build_deploy_verification,
    plan_deploy_steps,
    run_deploy_lane,
)
from tools.runners.lane_dispatch import (
    DEPLOY_LANE,
    dispatch_deploy_lane,
    is_lane_lit,
)

# ── production guard ────────────────────────────────────────────────────


def test_assert_dry_run_allows_plan_and_dry_run_flags():
    assert_dry_run(("terraform", "plan", "-input=false"))
    assert_dry_run(("kubectl", "apply", "--dry-run=server", "-f", "."))
    assert_dry_run(("helm", "template", "."))
    assert_dry_run(("terraform", "validate"))


def test_assert_dry_run_rejects_real_applies():
    for argv in (
        ("terraform", "apply", "-auto-approve"),
        ("terraform", "destroy"),
        ("helm", "upgrade", "rel", "."),
        ("helm", "install", "rel", "."),
        ("kubectl", "rollout", "restart", "deploy/x"),
        ("argocd", "app", "sync", "x"),
    ):
        with pytest.raises(ProductionApplyError):
            assert_dry_run(argv)


def test_plan_never_assembles_a_production_apply():
    # No matter the inputs, every assembled step is dry-run (asserted internally).
    steps = plan_deploy_steps(
        ["main.tf", "k8s/deploy.yaml", "charts/app/Chart.yaml"],
        required_scans=["iac-scan", "sast"],
    )
    for s in steps:
        assert_dry_run(s.argv)  # would raise if any apply slipped in
    names = {s.name for s in steps}
    assert "terraform-plan" in names
    assert "kubectl-apply-dry-run" in names


# ── step planning ───────────────────────────────────────────────────────


def test_terraform_change_plans_validate_and_plan():
    steps = plan_deploy_steps(["infra/main.tf"])
    names = [s.name for s in steps]
    assert "terraform-validate" in names
    assert "terraform-plan" in names
    plan = next(s for s in steps if s.name == "terraform-plan")
    assert plan.level == "VAL-2"
    # terraform plan must never auto-approve / apply
    assert "apply" not in plan.argv and "-auto-approve" not in plan.argv


def test_helm_chart_plans_template_and_dry_run():
    steps = plan_deploy_steps(["deploy/charts/app/Chart.yaml"])
    names = {s.name for s in steps}
    assert "helm-template-kubeconform" in names
    assert "kubectl-apply-dry-run" in names


def test_required_scans_force_scanners_even_without_glob_match():
    # No .tf files, but the policy demands an IaC scan -> forced on.
    steps = plan_deploy_steps(["app/Chart.yaml"], required_scans=["iac-scan"])
    names = {s.name for s in steps}
    assert "tfsec" in names or "trivy" in names
    forced = [s for s in steps if s.name in ("tfsec", "trivy")]
    assert any(not s.optional for s in forced), "forced scans must be non-optional"


def test_no_deploy_files_yields_no_steps():
    assert plan_deploy_steps(["README.md", "src/app.py"]) == []


# ── running the lane (injected runner) ──────────────────────────────────


def _ok(_argv):
    return StepResult(name="x", level="VAL-0", status="passed", returncode=0)


def _fail(_argv):
    return StepResult(name="x", level="VAL-0", status="failed", returncode=1, reason="boom")


def test_run_deploy_lane_all_pass_reaches_dry_run_ceiling():
    res = run_deploy_lane(
        ["main.tf"],
        run_fn=_ok,
        tool_available=lambda _t: True,
    )
    assert res.ok is True
    block = res.verification
    # terraform-plan is VAL-2 and passed -> achieved VAL-2 (the dry-run ceiling).
    assert block["achieved_level"] == "VAL-2"
    # The lane NEVER claims a production apply.
    val4 = next(lvl for lvl in block["levels"] if lvl["level"] == "VAL-4")
    assert val4["status"] == "not_run"
    assert "never run autonomously" in val4["reason"]


def test_run_deploy_lane_failure_caps_the_level():
    res = run_deploy_lane(
        ["main.tf"],
        run_fn=_fail,
        tool_available=lambda _t: True,
    )
    assert res.ok is False
    # A failed VAL-2 step caps the achieved level back down (gate recomputes truth).
    assert res.verification["achieved_level"] in ("VAL-0",)


def test_missing_tool_is_honest_not_run_not_a_silent_pass():
    res = run_deploy_lane(
        ["main.tf"],
        run_fn=_ok,
        tool_available=lambda _t: False,  # nothing installed
    )
    statuses = {s.name: s.status for s in res.steps}
    assert all(v == "not_run" for v in statuses.values())
    assert all(s.reason for s in res.steps if s.status == "not_run")
    # With nothing run, the block is honestly not verified beyond static.
    assert res.verification["achieved_level"] == "VAL-0"


def test_build_deploy_verification_never_overclaims_target():
    # Even if a caller asks for VAL-4, the deploy lane caps at the dry-run ceiling.
    block = build_deploy_verification(
        [StepResult(name="terraform-plan", level="VAL-2", status="passed")],
        target_level="VAL-4",
    )
    assert block["target_level"] in ("VAL-2",)
    assert block.get("mode") == "dry-run"
    assert "VAL-4" not in {lvl["level"] for lvl in block["levels"] if lvl["status"] == "passed"}


# ── dispatch integration ────────────────────────────────────────────────


def test_deploy_lane_is_lit():
    assert is_lane_lit(DEPLOY_LANE) is True


def test_dispatch_deploy_lane_returns_steps_and_verification():
    result = dispatch_deploy_lane(
        files=["main.tf"],
        run_fn=_ok,
        tool_available=lambda _t: True,
    )
    assert result.lane == DEPLOY_LANE
    assert result.runner_used == "deploy"
    assert result.deploy_result is not None
    assert result.deploy_result.verification["achieved_level"] == "VAL-2"


def test_kubectl_step_targets_detected_manifests_not_dot():
    """kubectl apply --dry-run=server points at the DETECTED k8s files, not `-f .`
    (which reads only the worktree root and errors on nested manifests, #603)."""
    from tools.runners.deploy_runner import plan_deploy_steps

    steps = plan_deploy_steps(["k8s/base/deploy.yaml", "k8s/base/svc.yaml"])
    kubectl = next(s for s in steps if s.name == "kubectl-apply-dry-run")
    assert kubectl.argv == (
        "kubectl", "apply", "--dry-run=server",
        "-f", "k8s/base/deploy.yaml", "-f", "k8s/base/svc.yaml",
    )
    assert "." not in kubectl.argv  # never the bare-root read
