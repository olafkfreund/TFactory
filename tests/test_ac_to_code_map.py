"""Tests for ac_to_code_map precise targeting (#248, epic #244)."""

from __future__ import annotations

import json

import pytest
from agents.task_contract import TfactoryProfile, ac_targets
from prompts_pkg.prompts import _build_contract_profile_block


def _profile(ac_map):
    return TfactoryProfile(lanes=("unit",), ac_to_code_map=ac_map)


# ─── accessor ────────────────────────────────────────────────────────────


def test_ac_targets_returns_files():
    p = _profile({"AC-1": ("src/login.py:login", "src/auth.py")})
    assert ac_targets(p, "AC-1") == ("src/login.py:login", "src/auth.py")


def test_ac_targets_missing_and_none():
    assert ac_targets(_profile({}), "AC-9") == ()
    assert ac_targets(None, "AC-1") == ()


# ─── prompt rendering ────────────────────────────────────────────────────


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "context").mkdir()
    return tmp_path


def _write(spec, ac_map):
    contract = {"contract_version": "2", "tfactory": {"lanes": ["unit"], "ac_to_code_map": ac_map}}
    (spec / "context" / "aifactory_plan.json").write_text(json.dumps(contract))


def test_block_renders_each_ac(spec):
    _write(spec, {"AC-1": ["src/a.py:f"], "AC-2": ["src/b.py"]})
    block = _build_contract_profile_block(spec)
    assert "`AC-1` → src/a.py:f" in block
    assert "`AC-2` → src/b.py" in block
    assert "one phase per AC" in block


def test_block_caps_large_map(spec):
    big = {f"AC-{i}": [f"f{i}.py"] for i in range(30)}
    _write(spec, big)
    block = _build_contract_profile_block(spec)
    assert "and 10 more" in block  # 30 - 20 cap
    assert "`AC-0` →" in block


def test_block_handles_empty_target_list(spec):
    _write(spec, {"AC-1": []})
    block = _build_contract_profile_block(spec)
    assert "(no files listed)" in block
