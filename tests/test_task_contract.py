"""Tests for the RFC-0002 task-contract reader (#245, epic #244).

Pure file IO — seeds context/*.json and asserts the parsed TfactoryProfile.
"""

from __future__ import annotations

import json

import pytest
from agents.task_contract import (
    TfactoryProfile,
    parse_tfactory_profile,
    read_task_contract,
    read_tfactory_profile,
)


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "context").mkdir()
    return tmp_path


_BLOCK = {
    "lanes": ["unit", "api", "browser", "security", "bogus"],
    "frameworks": {"unit": "pytest", "browser": "playwright"},
    "endpoints": {"api_base_url": "http://localhost:8000"},
    "docker_compose": "docker-compose.test.yml",
    "coverage_target": 0.85,
    "mutation_scope": ["src/core/**.py"],
    "security_scope": ["owasp:*"],
    "ac_to_code_map": {"AC-1": ["src/login.py:login"], "AC-2": ["src/auth.py"]},
}


def _contract(**extra):
    c = {"contract_version": "2", "correlation_key": "42", "tfactory": dict(_BLOCK)}
    c.update(extra)
    return c


# ─── parse ───────────────────────────────────────────────────────────────


def test_parse_full_block():
    p = parse_tfactory_profile(_contract())
    assert p.lanes == ("unit", "api", "browser", "security")  # "bogus" dropped
    assert p.frameworks == {"unit": "pytest", "browser": "playwright"}
    assert p.endpoints["api_base_url"] == "http://localhost:8000"
    assert p.docker_compose == "docker-compose.test.yml"
    assert p.coverage_target == 0.85
    assert p.mutation_scope == ("src/core/**.py",)
    assert p.security_scope == ("owasp:*",)
    assert p.ac_to_code_map["AC-1"] == ("src/login.py:login",)
    assert p.correlation_key == "42"


def test_parse_none_when_no_block():
    assert parse_tfactory_profile({"contract_version": "2"}) is None
    assert parse_tfactory_profile({}) is None
    assert parse_tfactory_profile(None) is None


def test_parse_none_when_block_empty():
    assert parse_tfactory_profile({"tfactory": {}}) is None


def test_parse_tolerant_of_partial_block():
    p = parse_tfactory_profile({"tfactory": {"lanes": ["unit"]}})
    assert p.lanes == ("unit",)
    assert p.frameworks == {}
    assert p.coverage_target is None


def test_parse_ignores_bad_types():
    p = parse_tfactory_profile(
        {"tfactory": {"lanes": "notalist", "coverage_target": "high", "frameworks": []}}
    )
    # lanes not a list → empty; coverage not numeric → None; frameworks not dict → {}
    assert p is None  # nothing usable → empty → None


# ─── read precedence ─────────────────────────────────────────────────────


def test_read_from_task_contract_json(spec):
    (spec / "context" / "task_contract.json").write_text(json.dumps(_contract()))
    doc = read_task_contract(spec)
    assert doc is not None and "tfactory" in doc


def test_read_from_aifactory_plan(spec):
    (spec / "context" / "aifactory_plan.json").write_text(json.dumps(_contract()))
    assert read_task_contract(spec) is not None


def test_task_contract_precedence(spec):
    # task_contract.json wins over aifactory_plan.json
    (spec / "context" / "task_contract.json").write_text(
        json.dumps(_contract(feature="A"))
    )
    (spec / "context" / "aifactory_plan.json").write_text(
        json.dumps(_contract(feature="B"))
    )
    assert read_task_contract(spec).get("feature") == "A"


def test_read_from_source_embedded(spec):
    (spec / "context" / "source.json").write_text(
        json.dumps({"branch": "x", "contract": _contract()})
    )
    doc = read_task_contract(spec)
    assert doc is not None and "tfactory" in doc


def test_read_none_when_absent(spec):
    assert read_task_contract(spec) is None
    assert read_tfactory_profile(spec) is None


def test_read_tfactory_profile_end_to_end(spec):
    (spec / "context" / "aifactory_plan.json").write_text(json.dumps(_contract()))
    p = read_tfactory_profile(spec)
    assert isinstance(p, TfactoryProfile)
    assert "unit" in p.lanes


def test_plain_plan_without_contract_is_ignored(spec):
    # An implementation_plan.json without tfactory/contract_version is not a contract.
    (spec / "context" / "aifactory_plan.json").write_text(
        json.dumps({"phases": [], "feature": "x"})
    )
    assert read_task_contract(spec) is None
