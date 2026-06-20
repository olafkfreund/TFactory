"""Tests for the RFC-0013 deploy-lane requirement policy (#447).

When a change is high-risk or production, the ``deploy`` lane must be in the
required set and a missing/insufficient deploy proof must block merge. Absent a
deployment block, behaviour is unchanged (back-compat).
"""

from __future__ import annotations

import json

from agents.deploy_policy import (
    DEPLOY_LANE,
    DeployRequirement,
    deploy_gate_for_spec,
    deploy_requirement_from_contract,
    deployment_block_from_contract,
    evaluate_deploy_gate,
    read_deploy_verification,
)
from tools.runners.deploy_runner import StepResult
from tools.runners.lane_dispatch import dispatch_deploy_lane

# ── back-compat: absent deployment block ────────────────────────────────


def test_absent_deployment_block_does_not_require_deploy_lane():
    for contract in (None, {}, {"execution": {"autonomy_tier": "low"}}):
        req = deploy_requirement_from_contract(contract)
        assert req.required is False
        assert req.lanes(("unit", "api")) == ("unit", "api")


def test_deployment_block_reader_is_tolerant():
    assert deployment_block_from_contract(None) is None
    assert deployment_block_from_contract({"deployment": "nope"}) is None
    assert deployment_block_from_contract({"deployment": {"risk_class": "low"}}) == {
        "risk_class": "low"
    }


# ── requirement derivation ──────────────────────────────────────────────


def test_low_and_medium_risk_do_not_force_deploy_lane():
    for risk in ("low", "medium"):
        contract = {"deployment": {"risk_class": risk, "production_classification": "internal"}}
        req = deploy_requirement_from_contract(contract)
        assert req.required is False


def test_high_risk_forces_deploy_lane():
    contract = {"deployment": {"risk_class": "high", "production_classification": "preprod"}}
    req = deploy_requirement_from_contract(contract)
    assert req.required is True
    assert "risk_class=high" in req.reasons
    assert req.lanes(("unit",)) == ("unit", DEPLOY_LANE)


def test_production_classification_forces_deploy_lane():
    contract = {"deployment": {"risk_class": "medium", "production_classification": "production"}}
    req = deploy_requirement_from_contract(contract)
    assert req.required is True
    assert "production_classification=production" in req.reasons


def test_lanes_is_idempotent_and_preserves_existing():
    req = DeployRequirement(required=True)
    assert req.lanes((DEPLOY_LANE, "unit")) == (DEPLOY_LANE, "unit")
    assert req.lanes(()) == (DEPLOY_LANE,)


# ── merge gate ──────────────────────────────────────────────────────────


def test_gate_never_blocks_when_not_required():
    verdict = evaluate_deploy_gate({"deployment": {"risk_class": "low"}}, None)
    assert verdict["required"] is False
    assert verdict["blocks_merge"] is False


def test_gate_blocks_when_required_but_no_verification():
    contract = {"deployment": {"risk_class": "high"}}
    verdict = evaluate_deploy_gate(contract, None)
    assert verdict["required"] is True
    assert verdict["blocks_merge"] is True
    assert "missing" in verdict["reason"]


def test_gate_blocks_when_dry_run_floor_not_reached():
    contract = {"deployment": {"risk_class": "high"}}
    verification = {"achieved_level": "VAL-0", "levels": [{"level": "VAL-0", "status": "failed"}]}
    verdict = evaluate_deploy_gate(contract, verification)
    assert verdict["blocks_merge"] is True


def test_gate_passes_on_dry_run_proof_but_prod_still_needs_human():
    contract = {"deployment": {"risk_class": "high", "production_classification": "production"}}
    verification = {"achieved_level": "VAL-2", "levels": [{"level": "VAL-2", "status": "passed"}]}
    verdict = evaluate_deploy_gate(contract, verification)
    assert verdict["blocks_merge"] is False
    assert verdict["human_approval_required"] is True
    assert "human-approval" in verdict["reason"]


def test_gate_passes_for_high_nonprod_with_dry_run_proof():
    contract = {"deployment": {"risk_class": "high", "production_classification": "preprod"}}
    verification = {"achieved_level": "VAL-2", "levels": [{"level": "VAL-2", "status": "passed"}]}
    verdict = evaluate_deploy_gate(contract, verification)
    assert verdict["blocks_merge"] is False
    assert verdict["human_approval_required"] is False


# ── spec-workspace reader ───────────────────────────────────────────────


def test_read_deploy_verification_missing_returns_none(tmp_path):
    assert read_deploy_verification(tmp_path) is None


def test_deploy_gate_for_spec_reads_persisted_block(tmp_path):
    findings = tmp_path / "findings"
    findings.mkdir()
    (findings / "deploy_verification.json").write_text(
        json.dumps({"achieved_level": "VAL-2", "levels": [{"level": "VAL-2", "status": "passed"}]})
    )
    contract = {"deployment": {"risk_class": "high", "production_classification": "preprod"}}
    verdict = deploy_gate_for_spec(contract, tmp_path)
    assert verdict["required"] is True
    assert verdict["blocks_merge"] is False
    assert verdict["achieved_level"] == "VAL-2"


def test_deploy_gate_for_spec_blocks_when_no_block_persisted(tmp_path):
    contract = {"deployment": {"risk_class": "high"}}
    verdict = deploy_gate_for_spec(contract, tmp_path)
    assert verdict["blocks_merge"] is True


def test_dispatch_persists_deploy_verification(tmp_path):
    def _ok(_argv):
        return StepResult(name="x", level="VAL-0", status="passed", returncode=0)

    dispatch_deploy_lane(
        files=["main.tf"],
        spec_dir=tmp_path,
        run_fn=_ok,
        tool_available=lambda _t: True,
    )
    out = tmp_path / "findings" / "deploy_verification.json"
    assert out.exists()
    block = json.loads(out.read_text())
    assert block["achieved_level"] == "VAL-2"
