"""Tests for the Planner's DECLARED TEST PROFILE injection (#246, epic #244).

When an RFC-0002 contract carries a tfactory block, the planner prompt gets an
authoritative profile block; absent → no block (inference path unchanged).
"""

from __future__ import annotations

import json

import pytest
from prompts_pkg.prompts import (
    _build_contract_profile_block,
    get_tfactory_planner_prompt,
    get_tfactory_planner_replan_prompt,
)


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "context").mkdir()
    return tmp_path


def _write_contract(spec, block):
    contract = {"contract_version": "2", "correlation_key": "7", "tfactory": block}
    (spec / "context" / "aifactory_plan.json").write_text(json.dumps(contract))


def test_no_block_when_no_contract(spec):
    assert _build_contract_profile_block(spec) == ""


def test_block_lists_lanes_and_frameworks(spec):
    _write_contract(spec, {
        "lanes": ["unit", "api"],
        "frameworks": {"unit": "pytest", "api": "supertest"},
        "endpoints": {"api_base_url": "http://localhost:8000"},
        "coverage_target": 0.9,
    })
    block = _build_contract_profile_block(spec)
    assert "DECLARED TEST PROFILE" in block
    assert "AUTHORITATIVE" in block
    assert "unit, api" in block
    assert "unit=pytest" in block
    assert "api_base_url=http://localhost:8000" in block
    assert "coverage_target**: 0.9" in block


def test_block_flags_security_out_of_scope(spec):
    _write_contract(spec, {"lanes": ["unit", "security"]})
    block = _build_contract_profile_block(spec)
    assert "security" in block
    assert "OUT OF SCOPE" in block


def test_block_mentions_ac_map(spec):
    _write_contract(spec, {"lanes": ["unit"], "ac_to_code_map": {"AC-1": ["a.py"], "AC-2": ["b.py"]}})
    block = _build_contract_profile_block(spec)
    assert "2 acceptance" in block


def test_planner_prompt_includes_profile(spec):
    _write_contract(spec, {"lanes": ["browser"], "frameworks": {"browser": "playwright"}})
    prompt = get_tfactory_planner_prompt(spec, spec)
    assert "DECLARED TEST PROFILE" in prompt
    assert "browser=playwright" in prompt


def test_planner_prompt_omits_profile_when_absent(spec):
    prompt = get_tfactory_planner_prompt(spec, spec)
    assert "DECLARED TEST PROFILE" not in prompt


# --- RFC-0011: difficulty tier raises the lane floor (#444) ---


def _write_full_contract(spec, contract):
    (spec / "context" / "aifactory_plan.json").write_text(json.dumps(contract))


def test_tier_adds_required_lanes_to_declared(spec):
    # A medium-tier contract whose tfactory block only declares unit still gets
    # the api+integration floor added by the tier (additive, never removes).
    _write_full_contract(spec, {
        "contract_version": "2",
        "execution": {"autonomy_tier": "medium"},
        "tfactory": {"lanes": ["unit"]},
    })
    block = _build_contract_profile_block(spec)
    assert "unit, api, integration" in block
    assert "RFC-0011" in block


def test_tier_only_contract_emits_lane_block(spec):
    # No tfactory block at all, but a hard tier => the floor lanes still render.
    _write_full_contract(spec, {
        "contract_version": "2",
        "execution": {"autonomy_tier": "hard"},
    })
    block = _build_contract_profile_block(spec)
    assert "DECLARED TEST PROFILE" in block
    assert "unit, api, integration, mutation" in block


def test_migration_forces_equivalence_lane_in_block(spec):
    _write_full_contract(spec, {
        "contract_version": "2",
        "execution": {"autonomy_tier": "low"},
        "workflow_type": "migration",
        "tfactory": {"lanes": ["unit"]},
    })
    block = _build_contract_profile_block(spec)
    assert "equivalence" in block


def test_absent_tier_leaves_declared_lanes_unchanged(spec):
    # Back-compat: no execution.autonomy_tier => declared lanes verbatim.
    _write_contract(spec, {"lanes": ["unit"]})
    block = _build_contract_profile_block(spec)
    assert "lanes** (generate these): unit" in block
    assert "RFC-0011" not in block


def test_replan_prompt_preserves_tier_lanes(spec):
    # The handback/replan loop must keep the tier lane floor in front of the planner.
    _write_full_contract(spec, {
        "contract_version": "2",
        "execution": {"autonomy_tier": "hard"},
        "tfactory": {"lanes": ["unit"]},
    })
    prompt = get_tfactory_planner_replan_prompt(spec, spec)
    assert "DECLARED TEST PROFILE" in prompt
    assert "unit, api, integration, mutation" in prompt
