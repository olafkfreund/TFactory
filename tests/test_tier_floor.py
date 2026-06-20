"""Tests for the RFC-0011 tier->VAL-floor / lane-set mapper (#444, epic #123)."""

from __future__ import annotations

import pytest
from agents.tier_floor import (
    change_mode_from_contract,
    lanes_for,
    tier_from_contract,
    val_floor_for,
)


@pytest.mark.parametrize(
    "tier,floor",
    [
        ("low", "VAL-1"),
        ("medium", "VAL-2"),
        ("hard", "VAL-3"),
        ("LOW", "VAL-1"),  # case-insensitive
        (" Hard ", "VAL-3"),  # whitespace-tolerant
    ],
)
def test_val_floor_for_each_tier(tier: str, floor: str) -> None:
    assert val_floor_for(tier) == floor


@pytest.mark.parametrize("absent", [None, "", "unknown", 3, "VAL-2"])
def test_val_floor_absent_or_unknown_is_none(absent: object) -> None:
    # Absent/unknown tier => None so the caller keeps its existing default.
    assert val_floor_for(absent) is None


@pytest.mark.parametrize(
    "tier,expected",
    [
        ("low", ("unit",)),
        ("medium", ("unit", "api", "integration")),
        ("hard", ("unit", "api", "integration", "mutation")),
    ],
)
def test_lanes_for_each_tier(tier: str, expected: tuple[str, ...]) -> None:
    assert lanes_for(tier) == expected


def test_lanes_absent_tier_is_empty() -> None:
    assert lanes_for(None) == ()
    assert lanes_for("nope") == ()


@pytest.mark.parametrize("tier", ["low", "medium", "hard"])
def test_migration_forces_equivalence_lane(tier: str) -> None:
    lanes = lanes_for(tier, change_mode="migration")
    assert "equivalence" in lanes
    # equivalence is appended on top of the tier's lanes, exactly once.
    assert lanes.count("equivalence") == 1
    assert lanes[-1] == "equivalence"


def test_migration_forces_equivalence_even_without_tier() -> None:
    # A rewrite must always prove parity, even if the tier is absent.
    assert lanes_for(None, change_mode="migration") == ("equivalence",)


def test_non_migration_change_mode_adds_nothing() -> None:
    assert lanes_for("medium", change_mode="feature") == ("unit", "api", "integration")
    assert lanes_for("medium", change_mode=None) == ("unit", "api", "integration")


def test_migration_case_insensitive() -> None:
    assert "equivalence" in lanes_for("low", change_mode="MIGRATION")


def test_tier_from_contract_reads_execution_block() -> None:
    assert tier_from_contract({"execution": {"autonomy_tier": "hard"}}) == "hard"


@pytest.mark.parametrize(
    "contract",
    [
        None,
        {},
        {"execution": None},
        {"execution": {}},
        {"execution": {"autonomy_tier": "bogus"}},
        {"execution": "not-a-dict"},
    ],
)
def test_tier_from_contract_absent_is_none(contract: object) -> None:
    assert tier_from_contract(contract) is None  # type: ignore[arg-type]


def test_change_mode_explicit_field_wins() -> None:
    assert change_mode_from_contract({"change_mode": "Migration"}) == "migration"


def test_change_mode_falls_back_to_workflow_type() -> None:
    assert change_mode_from_contract({"workflow_type": "migration"}) == "migration"
    assert change_mode_from_contract({"workflow_type": "feature"}) is None


@pytest.mark.parametrize("contract", [None, {}, {"workflow_type": "refactor"}])
def test_change_mode_absent_is_none(contract: object) -> None:
    assert change_mode_from_contract(contract) is None  # type: ignore[arg-type]


def test_end_to_end_hard_migration_from_contract() -> None:
    contract = {
        "execution": {"autonomy_tier": "hard"},
        "workflow_type": "migration",
    }
    tier = tier_from_contract(contract)
    mode = change_mode_from_contract(contract)
    assert val_floor_for(tier) == "VAL-3"
    assert lanes_for(tier, mode) == (
        "unit",
        "api",
        "integration",
        "mutation",
        "equivalence",
    )
