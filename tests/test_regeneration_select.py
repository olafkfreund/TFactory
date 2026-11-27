"""Fail-closed selection and judge evidence for regeneration (#1701)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents.evaluator_verdicts import _validate_verdicts
from agents.regeneration import (
    MIN_QUOTE_CHARS,
    REASON_SPEC_CONTRADICTION,
    REASON_TEST_QUALITY,
    Candidate,
    eligible_candidates,
    load_ac_texts,
    load_vote_docs,
    normalize_quote,
    select_candidates,
)
from agents.stability_runner import StabilityVerdict

REJECT = {"test_id": "test-a", "verdict": "reject"}
AC = "Return an empty list when no matches exist, preserving the input."
QUOTE = "when no matches exist"
OTHER_AC = "Return a validation error when the input is malformed."


def _bundle(
    verdict: object = StabilityVerdict.CONSISTENT_FAIL,
    failure_kind: str | None = "assertion",
    test_id: str = "test-a",
) -> SimpleNamespace:
    return SimpleNamespace(
        test_id=test_id,
        stability=SimpleNamespace(verdict=verdict, failure_kind=failure_kind),
    )


def _vote(**fields: Any) -> dict[str, Any]:
    return {"verdicts": [{**REJECT, **fields}]}


def _quoted_vote(quote: object = QUOTE) -> dict[str, Any]:
    return _vote(spec_contradiction={"criterion": quote})


@pytest.mark.parametrize("verdict", [StabilityVerdict.STABLE, "flaky"])
def test_test_quality_candidates(verdict: object) -> None:
    assert select_candidates([REJECT], [_bundle(verdict)], [], []) == [
        Candidate("test-a", REASON_TEST_QUALITY)
    ]


@pytest.mark.parametrize("calls", [2, 3])
def test_unanimous_votes_match_one_criterion(calls: int) -> None:
    first_quote = " \tRETURN an\nEMPTY LIST  "
    docs = [_quoted_vote(first_quote), _quoted_vote()] + [_quoted_vote()] * (calls - 2)
    assert select_candidates([REJECT], [_bundle()], docs, [OTHER_AC, AC]) == [
        Candidate("test-a", REASON_SPEC_CONTRADICTION, first_quote, AC)
    ]
    assert normalize_quote(first_quote) == "return an empty list"
    assert normalize_quote(123) == ""


@pytest.mark.parametrize(
    "last_vote",
    [
        _vote(verdict="accept", spec_contradiction={"criterion": QUOTE}),
        _vote(),
        _vote(test_id="other-test"),
        _quoted_vote("This quote appears in neither acceptance criterion."),
        _quoted_vote(OTHER_AC),
        _quoted_vote(AC[: MIN_QUOTE_CHARS - 1]),
        _quoted_vote(""),
        _quoted_vote(" \t\n"),
        _quoted_vote(42),
        _vote(spec_contradiction=QUOTE),
        _vote(spec_contradiction={}),
        _vote(spec_contradiction=None),
        {"verdicts": "invalid"},
        {"verdicts": [None]},
        None,
    ],
)
def test_nonunanimous_or_invalid_evidence_keeps_reject(
    last_vote: dict[str, Any] | None,
) -> None:
    docs = [_quoted_vote(), _quoted_vote(), last_vote]
    assert select_candidates([REJECT], [_bundle()], docs, [AC, OTHER_AC]) == []


@pytest.mark.parametrize("calls", [0, 1])
def test_too_few_vote_calls(calls: int) -> None:
    docs = [_quoted_vote()] * calls
    assert select_candidates([REJECT], [_bundle()], docs, [AC]) == []


@pytest.mark.parametrize(
    "bundle",
    [
        _bundle(StabilityVerdict.ERROR),
        _bundle("unknown"),
        _bundle([]),
        _bundle(failure_kind="import"),
        _bundle(failure_kind="environment"),
        _bundle(failure_kind="app_not_healthy"),
        _bundle(failure_kind=None),
        SimpleNamespace(test_id="test-a", stability=None),
        _bundle("stable", test_id="other-test"),
        None,
    ],
)
def test_ineligible_or_missing_stability(bundle: SimpleNamespace | None) -> None:
    bundles = [] if bundle is None else [bundle]
    assert select_candidates([REJECT], bundles, [_quoted_vote()] * 2, [AC]) == []


@pytest.mark.parametrize(
    "row",
    [
        {**REJECT, "verdict": "not_run"},
        {**REJECT, "verdict": "accept"},
        {**REJECT, "verdict": "flag"},
        {**REJECT, "judged": False},
        {"verdict": "reject"},
        {**REJECT, "test_id": ["test-a"]},
        "test-a",
        None,
    ],
)
def test_ineligible_or_malformed_final_row(row: Any) -> None:
    assert select_candidates([row], [_bundle("stable")], [], []) == []


def test_unreadable_stability_keeps_reject() -> None:
    class BrokenStability:
        @property
        def verdict(self) -> str:
            raise ValueError("invalid evidence")

    bundle = SimpleNamespace(test_id="test-a", stability=BrokenStability())
    assert select_candidates([REJECT], [bundle], [], []) == []


def test_candidates_are_sorted_deduplicated_and_only_selection_is_capped() -> None:
    ids = ["e", "b", "d", "a", "c", "a"]
    rows = [{**REJECT, "test_id": test_id} for test_id in ids]
    bundles = [_bundle("stable", test_id=test_id) for test_id in set(ids)]
    assert [c.test_id for c in eligible_candidates(rows, bundles, [], [])] == list(
        "abcde"
    )
    assert [c.test_id for c in select_candidates(rows, bundles, [], [])] == list("abc")


def test_load_vote_docs_keeps_call_positions(tmp_path: Path) -> None:
    doc = _quoted_vote()
    (tmp_path / "verdicts.vote1.json").write_text("invalid JSON")
    (tmp_path / "verdicts.vote2.json").write_text(json.dumps(doc))
    (tmp_path / "verdicts.vote3.json").write_text("[]")
    (tmp_path / "verdicts.vote4.json").mkdir()
    (tmp_path / "verdicts.vote5.json").write_bytes(b"\xff")
    assert load_vote_docs(tmp_path, 6) == [None, None, doc, None, None, None]


def test_load_ac_texts_from_plan_and_contract(tmp_path: Path) -> None:
    plan = {
        "phases": [{"name": f"AC#1: {AC}"}, {"name": "Replan AC#1"}, None],
        "final_acceptance": ["Plan criterion", 123],
    }
    (tmp_path / "test_plan.json").write_text(json.dumps(plan))
    context = tmp_path / "context"
    context.mkdir()
    (context / "task_contract.json").write_text(
        json.dumps({"contract_version": "2.0", "final_acceptance": [OTHER_AC]})
    )
    assert load_ac_texts(tmp_path) == [AC, "Plan criterion", OTHER_AC]
    (context / "task_contract.json").write_bytes(b"\xff")
    assert load_ac_texts(tmp_path) == [AC, "Plan criterion"]


def test_load_ac_texts_missing_or_unreadable_plan(tmp_path: Path) -> None:
    assert load_ac_texts(tmp_path) == []
    (tmp_path / "test_plan.json").write_bytes(b"\xff")
    assert load_ac_texts(tmp_path) == []


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"spec_contradiction": None},
        {"spec_contradiction": {"criterion": "x" * 500}},
    ],
)
def test_verdict_validator_accepts_optional_contradiction(
    tmp_path: Path, fields: dict[str, Any]
) -> None:
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(_vote(**fields)))
    assert _validate_verdicts(path) == (True, "", 1)


@pytest.mark.parametrize(
    "value", [QUOTE, {}, {"criterion": ""}, {"criterion": 42}, {"criterion": "x" * 501}]
)
def test_verdict_validator_rejects_invalid_contradiction(
    tmp_path: Path, value: Any
) -> None:
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(_vote(spec_contradiction=value)))
    ok, error, count = _validate_verdicts(path)
    assert not ok
    assert "spec_contradiction" in error
    assert count == 0
