"""Contradictory criteria are UNVERIFIABLE, not failed (#896).

"Failed" and "cannot be determined" are different verdicts, and reporting the
second as the first is a false negative that looks like a finding. The load-
bearing assertions here are the two the issue names:

  - a contradictory criterion produces UNVERIFIABLE, and
  - a genuinely failing one still produces FAIL.

Both must hold at once, or the grade is just a rename of `unverified`.
"""

from __future__ import annotations

import json

from agents.ac_fidelity import build_ac_ledger, render_markdown
from agents.criterion_conflict import (
    detect_criterion_conflicts,
    read_criterion_conflicts,
    write_criterion_conflicts,
)
from agents.traceability import build_traceability

# AC#1 and AC#2 are the same sentence with different totals: no test can satisfy
# both. AC#3 is a perfectly good criterion whose test genuinely fails.
_CONTRADICTORY = [
    ("AC#1", "the order total is 11.76"),
    ("AC#2", "the order total is 11.75"),
    ("AC#3", "the cart shows 3 items"),
]


def _plan(criteria) -> dict:
    return {
        "phases": [
            {
                "phase": i + 1,
                "name": f"{ac_id}: {text}",
                "subtasks": [{"id": f"st{i}"}],
            }
            for i, (ac_id, text) in enumerate(criteria)
        ]
    }


def _rejected(idx: int) -> dict:
    return {"test_id": f"st{idx}", "test_file": f"tests/t{idx}.py", "verdict": "reject"}


def _accepted(idx: int) -> dict:
    return {"test_id": f"st{idx}", "test_file": f"tests/t{idx}.py", "verdict": "accept"}


# ── detection ────────────────────────────────────────────────────────────────


def test_detects_same_statement_with_different_values() -> None:
    conflicts = detect_criterion_conflicts(_CONTRADICTORY)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert {conflict.ac_id, conflict.conflicting_ac_id} == {"AC#1", "AC#2"}
    # The values AS SIGNED, per criterion — the concrete contradiction, so a
    # human can see which number the spec must be corrected to.
    assert conflict.values == {"AC#1": ["11.76"], "AC#2": ["11.75"]}
    assert "11.76" in conflict.contradiction
    assert "11.75" in conflict.contradiction


def test_consistent_criteria_produce_no_conflict() -> None:
    # The false-positive budget is the point: a spurious conflict would cap a
    # run's VAL claim on a spec that was fine.
    assert (
        detect_criterion_conflicts(
            [
                ("AC#1", "the order total is 11.76"),
                ("AC#2", "the shipping fee is 4.99"),
                ("AC#3", "the cart shows 3 items"),
            ]
        )
        == []
    )


def test_same_statement_with_the_same_value_is_not_a_conflict() -> None:
    # A duplicated criterion is redundant, not contradictory.
    assert (
        detect_criterion_conflicts(
            [("AC#1", "the total is 10.00"), ("AC#2", "the total is 10.0")]
        )
        == []
    )


def test_criteria_with_no_numeric_value_are_never_conflicting() -> None:
    assert (
        detect_criterion_conflicts(
            [("AC#1", "the page loads"), ("AC#2", "the page loads")]
        )
        == []
    )


# ── the two verdicts the issue names ─────────────────────────────────────────


def test_contradictory_criterion_is_unverifiable_and_failing_one_is_not() -> None:
    """The load-bearing assertion: both verdicts, in one ledger.

    AC#1/AC#2 contradict -> UNVERIFIABLE. AC#3 is satisfiable but its test was
    rejected -> UNVERIFIED (a real finding about the run). If the grade did not
    discriminate these, it would just be `unverified` with a new name.
    """
    conflicts = [c.to_dict() for c in detect_criterion_conflicts(_CONTRADICTORY)]
    ledger = build_ac_ledger(
        _plan(_CONTRADICTORY),
        [_rejected(0), _rejected(1), _rejected(2)],
        conflicts=conflicts,
    )
    by_id = {ac["ac_id"]: ac for ac in ledger["acceptance"]}

    assert by_id["AC#1"]["status"] == "unverifiable"
    assert by_id["AC#2"]["status"] == "unverifiable"
    assert by_id["AC#3"]["status"] == "unverified"

    assert ledger["summary"]["unverifiable"] == 2
    assert ledger["summary"]["unverified"] == 1
    assert ledger["summary"]["all_acs_verified"] is False


def test_unverifiable_can_never_be_verified() -> None:
    # An accepted test against an unsatisfiable criterion proves the TEST wrong,
    # not the criterion right.
    conflicts = [c.to_dict() for c in detect_criterion_conflicts(_CONTRADICTORY)]
    ledger = build_ac_ledger(
        _plan(_CONTRADICTORY),
        [_accepted(0), _accepted(1), _accepted(2)],
        conflicts=conflicts,
    )
    by_id = {ac["ac_id"]: ac for ac in ledger["acceptance"]}

    assert by_id["AC#1"]["status"] == "unverifiable"
    assert by_id["AC#3"]["status"] == "verified"
    assert ledger["summary"]["all_acs_verified"] is False


def test_no_conflicts_leaves_every_grade_unchanged() -> None:
    # Back-compat: the fourth grade must be inert when nothing is contradictory.
    ledger = build_ac_ledger(_plan(_CONTRADICTORY), [_accepted(0), _rejected(1)])
    statuses = {ac["ac_id"]: ac["status"] for ac in ledger["acceptance"]}

    assert statuses == {"AC#1": "verified", "AC#2": "unverified", "AC#3": "unverified"}
    assert ledger["summary"]["unverifiable"] == 0


# ── it caps the VAL claim exactly as a failure does ──────────────────────────


def test_unverifiable_caps_the_val_claim_like_a_failure() -> None:
    conflicts = [c.to_dict() for c in detect_criterion_conflicts(_CONTRADICTORY)]
    ledger = build_ac_ledger(
        _plan(_CONTRADICTORY),
        [_accepted(0), _accepted(1), _accepted(2)],
        conflicts=conflicts,
    )
    rows = build_traceability(ledger, {"achieved_level": "VAL-2"})
    by_id = {r["ac_id"]: r for r in rows}

    # `failed` is the only status the gate treats as capping — `skipped` and
    # `not_run` would let the run keep its ceiling on an unsatisfiable spec.
    assert by_id["AC#1"]["status"] == "failed"
    assert by_id["AC#1"]["val_level"] == "VAL-0"
    # The genuinely satisfiable, genuinely passing AC still earns the level.
    assert by_id["AC#3"]["status"] == "passed"
    assert by_id["AC#3"]["val_level"] == "VAL-2"


def test_unverifiable_with_no_tests_is_still_failed_not_not_run() -> None:
    # "no test ran" understates a criterion that is provably unsatisfiable, and
    # reads as a coverage gap somebody could close.
    conflicts = [c.to_dict() for c in detect_criterion_conflicts(_CONTRADICTORY)]
    ledger = build_ac_ledger(_plan(_CONTRADICTORY), [], conflicts=conflicts)
    rows = {
        r["ac_id"]: r for r in build_traceability(ledger, {"achieved_level": "VAL-2"})
    }

    assert rows["AC#1"]["status"] == "failed"
    assert rows["AC#3"]["status"] == "not_run"


# ── it is reported, not just recorded ────────────────────────────────────────


def test_markdown_names_the_conflicting_criterion() -> None:
    conflicts = [c.to_dict() for c in detect_criterion_conflicts(_CONTRADICTORY)]
    md = render_markdown(
        build_ac_ledger(_plan(_CONTRADICTORY), [_rejected(0)], conflicts=conflicts)
    )

    assert "AC#1 [UNVERIFIABLE]" in md
    assert "CONFLICTS WITH AC#2" in md
    assert "unverifiable: 2" in md
    # It must say whose defect this is — the spec's, not the tests'.
    assert "defect in the specification" in md


def test_markdown_is_unchanged_when_nothing_conflicts() -> None:
    md = render_markdown(build_ac_ledger(_plan(_CONTRADICTORY), [_accepted(0)]))

    assert "UNVERIFIABLE" not in md
    assert "unverifiable" not in md


# ── the finding round-trips ──────────────────────────────────────────────────


def test_finding_is_written_and_read_back(tmp_path) -> None:
    conflicts = [c.to_dict() for c in detect_criterion_conflicts(_CONTRADICTORY)]
    written = write_criterion_conflicts(tmp_path, conflicts)

    assert written is not None
    assert written["gating"] is True
    on_disk = json.loads(
        (tmp_path / "findings" / "criterion_conflict.json").read_text()
    )
    assert on_disk["conflicts"][0]["values"] == {"AC#1": ["11.76"], "AC#2": ["11.75"]}
    assert read_criterion_conflicts(tmp_path) == written


def test_no_finding_file_when_there_are_no_conflicts(tmp_path) -> None:
    # An empty finding would assert "we checked and it is clean" on runs where
    # detection never ran.
    assert write_criterion_conflicts(tmp_path, []) is None
    assert not (tmp_path / "findings" / "criterion_conflict.json").exists()
    assert read_criterion_conflicts(tmp_path) is None


# ── end to end through the triager ───────────────────────────────────────────


def test_triager_detects_writes_and_halts_on_a_contradiction(tmp_path) -> None:
    """Plan in -> finding on disk -> UNVERIFIABLE ledger -> envelope halt.

    Detection runs off the PLAN, not off a generator having flagged the
    contradiction during a stochastic step — the issue's own evidence is that
    the rewrite is stochastic, which is the argument for first-class reporting.
    """
    from agents.triager import _apply_gate_annotations, _detect_criterion_conflicts

    (tmp_path / "findings").mkdir(parents=True)
    plan = _plan(_CONTRADICTORY)

    conflicts = _detect_criterion_conflicts(tmp_path, plan)
    assert len(conflicts) == 1
    assert (tmp_path / "findings" / "criterion_conflict.json").is_file()

    ledger = build_ac_ledger(plan, [_rejected(2)], conflicts=conflicts)
    assert ledger["summary"]["unverifiable"] == 2

    envelope: dict = {"outcome": "success"}
    _apply_gate_annotations(tmp_path, envelope)  # type: ignore[arg-type]

    # A human sees the contradiction NAMED, not a stuck subtask to infer it from.
    assert envelope["outcome"] == "human_review"
    assert envelope["halt_reason"].startswith("criterion_conflict:")
    assert envelope["criterion_conflict"]["conflicts"][0]["values"] == {
        "AC#1": ["11.76"],
        "AC#2": ["11.75"],
    }


def test_triager_leaves_a_clean_run_alone(tmp_path) -> None:
    from agents.triager import _apply_gate_annotations, _detect_criterion_conflicts

    (tmp_path / "findings").mkdir(parents=True)
    clean = [("AC#1", "the order total is 11.76"), ("AC#2", "the fee is 4.99")]

    assert _detect_criterion_conflicts(tmp_path, _plan(clean)) == []

    envelope: dict = {"outcome": "success"}
    _apply_gate_annotations(tmp_path, envelope)  # type: ignore[arg-type]

    assert envelope["outcome"] == "success"
    assert "criterion_conflict" not in envelope
