"""Tests for PFactory governed test-target pickup (#195, epic #193).

Covers the recognition gate (GitHub issue labels + requirements.json), the
priority→horizon mapping, and the enqueue seam — per the tag-taxonomy v1
contract (PFactory repo docs/tag-taxonomy.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from integrations.pfactory import (
    PickupDecision,
    classify_issue,
    classify_labels,
    classify_requirements,
    pickup_issue,
    pickup_requirements,
    priority_to_horizon,
)


@dataclass
class _Issue:
    """Minimal IssueData-like stand-in (number/title/body/labels)."""

    number: int
    title: str = "Test the widget"
    body: str = ""
    labels: list[str] = field(default_factory=list)


# ─── core label gate ────────────────────────────────────────────────────


def test_governed_testing_child_is_picked_up() -> None:
    d = classify_labels(["pfactory", "handoff:tfactory", "type:testing", "priority:p2"])
    assert d.picked_up is True
    assert d.source == "issue"
    assert d.priority == "p2" and d.horizon == "later"
    assert d.also_aifactory is False and d.is_epic is False


def test_non_pfactory_issue_is_unaffected() -> None:
    d = classify_labels(["bug", "backend"])
    assert d.picked_up is False
    assert "no 'pfactory'" in d.reason


def test_pfactory_but_not_routed_to_tfactory_is_skipped() -> None:
    d = classify_labels(["pfactory", "handoff:aifactory", "type:infra"])
    assert d.picked_up is False
    assert "handoff:tfactory" in d.reason


def test_child_routed_to_both_factories_is_a_test_target() -> None:
    d = classify_labels(["pfactory", "handoff:tfactory", "handoff:aifactory"])
    assert d.picked_up is True
    assert d.also_aifactory is True  # TFactory still owns only the test pass


def test_epic_marker_surfaced() -> None:
    d = classify_labels(["pfactory", "handoff:tfactory", "epic"])
    assert d.picked_up is True and d.is_epic is True


def test_labels_are_case_and_whitespace_insensitive() -> None:
    d = classify_labels([" PFactory ", "Handoff:TFactory", 123, None])
    assert d.picked_up is True


def test_handoff_tfactory_without_type_testing_still_picked_up() -> None:
    # Contract: handoff:tfactory is also carried by non-testing children that
    # need an independent test pass — type:testing is not required.
    d = classify_labels(["pfactory", "handoff:tfactory", "type:feature"])
    assert d.picked_up is True


# ─── priority → horizon ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "priority,horizon",
    [("p0", "now"), ("p1", "next"), ("p2", "later"), ("p3", "later"), ("P0", "now")],
)
def test_priority_to_horizon_mapping(priority: str, horizon: str) -> None:
    assert priority_to_horizon(priority) == horizon


def test_priority_to_horizon_unknown_or_absent() -> None:
    assert priority_to_horizon(None) is None
    assert priority_to_horizon("p9") is None


# ─── classify_issue (object + dict) ─────────────────────────────────────


def test_classify_issue_object() -> None:
    issue = _Issue(number=412, labels=["pfactory", "handoff:tfactory", "priority:p0"])
    d = classify_issue(issue)
    assert d.picked_up is True
    assert d.issue_number == 412 and d.horizon == "now"


def test_classify_issue_dict() -> None:
    d = classify_issue(
        {"number": 7, "labels": ["pfactory", "handoff:tfactory"], "body": ""}
    )
    assert d.picked_up is True and d.issue_number == 7


def test_taxonomy_version_read_from_meta_block() -> None:
    body = "Acceptance...\n<!-- pfactory:meta\nplan_id: 001-x\ntaxonomy: v1\n-->"
    issue = _Issue(number=1, body=body, labels=["pfactory", "handoff:tfactory"])
    assert classify_issue(issue).taxonomy_version == "v1"


# ─── requirements.json path ─────────────────────────────────────────────


def test_requirements_mirrored_labels_path() -> None:
    req = {
        "title": "Test orders",
        "metadata": {"plan_id": "001-orders", "labels": ["pfactory", "handoff:tfactory"]},
    }
    d = classify_requirements(req)
    assert d.picked_up is True and d.source == "requirements" and d.plan_id == "001-orders"


def test_requirements_explicit_handoff_path() -> None:
    req = {
        "metadata": {
            "plan_id": "001-orders",
            "pfactory": True,
            "handoffs": ["tfactory", "aifactory"],
            "priority": "p1",
            "taxonomy": "v1",
        }
    }
    d = classify_requirements(req)
    assert d.picked_up is True
    assert d.also_aifactory is True and d.horizon == "next"


def test_requirements_aifactory_only_is_skipped() -> None:
    req = {"metadata": {"pfactory": True, "handoffs": ["aifactory"], "taxonomy": "v1"}}
    d = classify_requirements(req)
    assert d.picked_up is False and "tfactory" in d.reason


def test_requirements_without_governance_marker_is_skipped() -> None:
    req = {"metadata": {"complexity": "simple", "githubIssueNumber": 9}}
    d = classify_requirements(req)
    assert d.picked_up is False


# ─── enqueue seam ───────────────────────────────────────────────────────


def test_pickup_issue_enqueues_governed_target() -> None:
    enqueued: list[dict] = []
    issue = _Issue(
        number=55,
        title="Test login flow",
        labels=["pfactory", "handoff:tfactory", "type:testing", "priority:p1"],
    )
    d = pickup_issue(issue, enqueue=enqueued.append)
    assert d.picked_up is True
    assert len(enqueued) == 1
    target = enqueued[0]
    assert target["issue_number"] == 55
    assert target["title"] == "Test login flow"
    assert target["horizon"] == "next"
    assert target["source"] == "issue"


def test_pickup_issue_does_not_enqueue_non_target() -> None:
    enqueued: list[dict] = []
    issue = _Issue(number=1, labels=["bug"])
    d = pickup_issue(issue, enqueue=enqueued.append)
    assert d.picked_up is False
    assert enqueued == []


def test_pickup_requirements_enqueues_governed_target() -> None:
    enqueued: list[dict] = []
    req = {
        "title": "Test orders",
        "description": "ACs...",
        "metadata": {"plan_id": "001-orders", "labels": ["pfactory", "handoff:tfactory"]},
    }
    d = pickup_requirements(req, enqueue=enqueued.append)
    assert d.picked_up is True and len(enqueued) == 1
    assert enqueued[0]["plan_id"] == "001-orders" and enqueued[0]["source"] == "requirements"


def test_decision_is_frozen() -> None:
    d = PickupDecision(False, "x")
    with pytest.raises(Exception):
        d.picked_up = True  # type: ignore[misc]


# ─── CLI ────────────────────────────────────────────────────────────────


def test_cli_governed_issue_exits_zero(tmp_path, capsys) -> None:
    import json

    from integrations.pfactory.pickup import _main

    f = tmp_path / "issue.json"
    # gh-style labels ([{"name": ...}]) must be normalized by the CLI.
    f.write_text(
        json.dumps(
            {"number": 9, "labels": [{"name": "pfactory"}, {"name": "handoff:tfactory"}]}
        )
    )
    rc = _main(["--issue", str(f)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["picked_up"] is True and out["issue_number"] == 9


def test_cli_non_target_exits_one(tmp_path, capsys) -> None:
    import json

    from integrations.pfactory.pickup import _main

    f = tmp_path / "issue.json"
    f.write_text(json.dumps({"number": 1, "labels": ["bug"]}))
    assert _main(["--issue", str(f)]) == 1
