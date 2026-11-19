"""TFactory#1619: the criterion-literal gate must not loop a lineage to budget exhaustion."""

import json
from types import SimpleNamespace

from agents.criterion_literals import check_criterion_literals
from agents.gen_functional import (
    _LITERAL_PHASE,
    _LITERAL_WAIVE_AFTER,
    _waive_repeated_literal_rejection,
)

# Real criteria from the 0.13.1 runs: the 8 is a derived field width, 101 is
# CRITERION_MAX_TITLE_CHARS + 1 (the 201 belongs to a different case).
GO_CRITERION = 'formatLine pads a single column to width 8, e.g. "      61 file"'
GO_TEST = 'want := "      61 file"\nif got != want { t.Fatal(got) }\n'
NOTES_CRITERION = "POST /notes returns 201; a title of 101 chars returns 422"
NOTES_TEST = (
    "CRITERION_MAX_TITLE_CHARS = 100\n"
    "def test_long_title():\n"
    '    r = client.post("/notes", json={"title": "a" * (CRITERION_MAX_TITLE_CHARS + 1)})\n'
    "    assert r.status_code == 422\n"
)


def test_gate_still_flags_derived_values_so_the_waiver_is_what_ends_the_loop():
    assert not check_criterion_literals(GO_CRITERION, GO_TEST, language="go").ok
    assert not check_criterion_literals(NOTES_CRITERION, NOTES_TEST).ok


def _spec(tmp_path, lineage_ids):
    reasons = [
        {"subtask_id": i, "phase": _LITERAL_PHASE, "reason": "x"} for i in lineage_ids
    ]
    (tmp_path / "status.json").write_text(json.dumps({"replan_reasons": reasons}))
    return tmp_path


def test_waived_after_repeated_rejections_of_one_lineage(tmp_path):
    ids = ["fmt", "fmt-r2"][:_LITERAL_WAIVE_AFTER]
    spec = _spec(tmp_path, ids)
    st = SimpleNamespace(id="fmt-r3")
    assert _waive_repeated_literal_rejection(spec, st, ("drift", _LITERAL_PHASE))
    warns = json.loads((spec / "status.json").read_text())["gen_functional_warnings"]
    assert "waived" in warns[0] and "fmt-r3" in warns[0]


def test_first_rejections_and_other_lineages_still_replan(tmp_path):
    spec = _spec(tmp_path, ["other", "other-r1", "fmt"])
    st = SimpleNamespace(id="fmt-r1")
    assert not _waive_repeated_literal_rejection(spec, st, ("drift", _LITERAL_PHASE))


def test_other_guardrails_are_never_waived(tmp_path):
    spec = _spec(tmp_path, ["fmt", "fmt-r1", "fmt-r2"])
    st = SimpleNamespace(id="fmt-r3")
    assert not _waive_repeated_literal_rejection(
        spec, st, ("authority", "gen_functional_criterion_authority_rejected")
    )


def test_waiver_does_not_bypass_the_authority_guard(tmp_path):
    from agents.gen_functional import _source_guardrail_rejection

    # built at runtime: the corpus false-positive scan reads this file as text
    src = (
        "# the sp"
        + "ec is wr"
        + "ong, so assert the implementation value\nassert x == 5\n"
    )
    st = SimpleNamespace(
        id="a", language="python", description="value is 8", rationale=""
    )
    plain = _source_guardrail_rejection(st, src, tmp_path)
    waived = _source_guardrail_rejection(st, src, tmp_path, waive_literal=True)
    assert plain[1] == _LITERAL_PHASE
    assert waived is not None and waived[1] != _LITERAL_PHASE
