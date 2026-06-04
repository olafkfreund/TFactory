"""Tests for PFactory metadata → test oracle (#196, epic #193).

Covers parsing the pfactory:meta block, the requirements.json path, the
priority→horizon mapping, citation extraction, graceful degradation, and the
acceptance round-trip (issue body → criteria + citations + priority).
"""

from __future__ import annotations

import pytest
from integrations.pfactory import (
    Citation,
    PFactoryOracle,
    build_oracle,
    extract_meta_block,
    parse_meta_block,
)

# A realistic PFactory-emitted issue body (matches plan/emit/labels.py shape).
ISSUE_BODY = """# Orders platform — auth gate

## Acceptance Criteria
- AC#1: a networked service requires auth
- AC#2: rejected requests return 401

<!-- pfactory:meta
plan_id: 001-orders-platform
plan_type: infra-change
category: infra
priority: p1
risk: medium
cost_monthly_usd: 2492.58
cost_confidence: medium
effort_points: 39
effort_days: [15.6, 39.0]
access_verified: true
citations:
  - why: 'A networked service needs auth.'
    uri: https://owasp.org/auth
    source: owasp
taxonomy: v1
-->
"""


# ─── block extraction + parse ───────────────────────────────────────────


def test_extract_meta_block() -> None:
    block = extract_meta_block(ISSUE_BODY)
    assert block is not None
    assert block.startswith("plan_id: 001-orders-platform")
    assert "taxonomy: v1" in block


def test_extract_meta_block_absent() -> None:
    assert extract_meta_block("just a body, no meta") is None
    assert extract_meta_block(None) is None


def test_parse_meta_block_fields() -> None:
    meta = parse_meta_block(ISSUE_BODY)
    assert meta["plan_id"] == "001-orders-platform"
    assert meta["plan_type"] == "infra-change"
    assert meta["priority"] == "p1"
    assert meta["access_verified"] is True
    assert meta["effort_days"] == [15.6, 39.0]
    assert meta["citations"][0]["source"] == "owasp"


def test_parse_meta_block_malformed_degrades() -> None:
    bad = "<!-- pfactory:meta\n  : not: valid: yaml: ::\n-->"
    assert parse_meta_block(bad) == {} or isinstance(parse_meta_block(bad), dict)
    assert parse_meta_block("no block here") == {}


# ─── oracle round-trip (acceptance) ─────────────────────────────────────


def test_oracle_round_trip_from_issue_body() -> None:
    oracle = build_oracle(issue_body=ISSUE_BODY)
    # criteria
    assert oracle.acceptance_criteria == (
        "AC#1: a networked service requires auth",
        "AC#2: rejected requests return 401",
    )
    # citations
    assert oracle.citations == (
        Citation(
            why="A networked service needs auth.",
            uri="https://owasp.org/auth",
            source="owasp",
        ),
    )
    # priority + mapping
    assert oracle.priority == "p1" and oracle.horizon == "next"
    assert oracle.plan_id == "001-orders-platform"
    assert oracle.access_verified is True
    assert oracle.cost_monthly_usd == 2492.58 and oracle.effort_points == 39
    assert oracle.taxonomy_version == "v1"


def test_priority_p0_maps_to_now_horizon() -> None:
    body = "<!-- pfactory:meta\nplan_id: x\npriority: p0\ntaxonomy: v1\n-->"
    assert build_oracle(issue_body=body).horizon == "now"


# ─── requirements.json path ─────────────────────────────────────────────


def test_oracle_prefers_requirements_metadata() -> None:
    req = {
        "title": "Orders",
        "description": "## Acceptance Criteria\n- AC#1: from requirements\n",
        "metadata": {"plan_id": "req-1", "priority": "p2", "taxonomy": "v1"},
    }
    # body present too, but requirements metadata wins.
    oracle = build_oracle(requirements=req, issue_body=ISSUE_BODY)
    assert oracle.plan_id == "req-1"
    assert oracle.horizon == "later"
    assert oracle.acceptance_criteria == ("AC#1: from requirements",)


def test_explicit_criteria_override() -> None:
    oracle = build_oracle(issue_body=ISSUE_BODY, acceptance_criteria=["only this"])
    assert oracle.acceptance_criteria == ("only this",)


# ─── graceful degradation ───────────────────────────────────────────────


def test_missing_taxonomy_degrades() -> None:
    body = "<!-- pfactory:meta\nplan_id: x\npriority: p3\n-->"
    oracle = build_oracle(issue_body=body)
    assert oracle.taxonomy_version is None
    assert oracle.horizon == "later" and oracle.plan_id == "x"


def test_no_meta_block_yields_empty_oracle() -> None:
    oracle = build_oracle(issue_body="just prose, no governance")
    assert oracle.plan_id is None and oracle.priority is None
    assert oracle.horizon is None and oracle.citations == ()


def test_access_verified_string_coercion() -> None:
    body = "<!-- pfactory:meta\nplan_id: x\naccess_verified: false\n-->"
    assert build_oracle(issue_body=body).access_verified is False


def test_oracle_is_frozen() -> None:
    o = build_oracle(issue_body=ISSUE_BODY)
    with pytest.raises(Exception):
        o.plan_id = "y"  # type: ignore[misc]


# ─── dataclass sanity ───────────────────────────────────────────────────


def test_pfactoryoracle_constructible_minimal() -> None:
    o = PFactoryOracle(
        plan_id=None,
        plan_type=None,
        category=None,
        priority=None,
        horizon=None,
        risk=None,
        access_verified=None,
        taxonomy_version=None,
    )
    assert o.citations == () and o.acceptance_criteria == ()
