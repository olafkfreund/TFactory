"""AC fidelity: per-criterion verified/unverified honesty (the trust lever)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from agents.ac_fidelity import build_ac_ledger, render_markdown
from agents.triager import _write_ac_fidelity

_PLAN = {
    "phases": [
        {"phase": 1, "name": "AC#1: GET / returns 200 HTML",
         "subtasks": [{"id": "root-200-unit"}, {"id": "root-200-browser"}]},
        {"phase": 2, "name": "AC#2: h1 says Hello",
         "subtasks": [{"id": "h1-browser"}]},
        {"phase": 3, "name": "AC#3: ping button updates result",
         "subtasks": [{"id": "ping-browser"}]},
        {"phase": 4, "name": "AC#4: health returns ok",
         "subtasks": [{"id": "health-unit"}]},
        {"phase": 5, "name": "replan-1",
         "subtasks": [{"id": "root-200-unit-r1"}]},
    ]
}
_VERDICTS = [
    {"test_id": "root-200-unit", "test_file": "tests/unit/root.py", "verdict": "accept"},
    {"test_id": "h1-browser", "test_file": "tests/e2e/h1.spec.ts", "verdict": "flag"},
    {"test_id": "ping-browser", "test_file": "tests/e2e/ping.spec.ts", "verdict": "reject"},
    # AC#4 has NO verdict -> unverified
]


def test_ledger_grades_each_ac_honestly():
    led = build_ac_ledger(_PLAN, _VERDICTS)
    by = {a["ac_id"]: a for a in led["acceptance"]}
    assert by["AC#1"]["status"] == "verified"        # has an accept
    assert by["AC#2"]["status"] == "flagged_only"    # only a flag
    assert by["AC#3"]["status"] == "unverified"      # only a reject
    assert by["AC#4"]["status"] == "unverified"      # no test at all
    assert "replan" not in {a["ac_id"] for a in led["acceptance"]}  # replan skipped


def test_summary_is_honest_fraction():
    led = build_ac_ledger(_PLAN, _VERDICTS)
    s = led["summary"]
    assert s["total"] == 4 and s["verified"] == 1
    assert s["verified_fraction"] == "1/4"
    assert s["all_acs_verified"] is False  # must NOT claim a full pass


def test_all_verified_true_only_when_every_ac_accepted():
    led = build_ac_ledger(_PLAN, _ALL_ACCEPTED)
    assert led["summary"]["all_acs_verified"] is True
    assert led["summary"]["verified_fraction"] == "4/4"


# ── the summary must not read as "the spec is verified" (#855) ─────────

_ALL_ACCEPTED = [
    {"test_id": t, "test_file": "f", "verdict": "accept"}
    for t in ("root-200-unit", "h1-browser", "ping-browser", "health-unit")
]

_SPEC_MD = """\
# Slug service

## Acceptance Criteria

- **AC#1:** GET /healthz returns 200

> Ingested from a markdown source by TFactory.

## Spec source (verbatim, as ingested)

```markdown
## Slug rules

- truncate to 200 characters BEFORE slugging

## Acceptance Criteria

- AC#1: GET /healthz returns 200

## Out of scope

- authentication
```
"""


def test_summary_never_claims_the_whole_spec_is_verified():
    """#855: 'all_verified' read as 'the spec is satisfied'; it never meant that."""
    s = build_ac_ledger(_PLAN, _ALL_ACCEPTED)["summary"]
    assert "all_verified" not in s, "the misreadable key must be gone, not aliased"
    assert s["all_acs_verified"] is True
    assert s["scope"] == "acceptance_criteria"


def test_summary_names_spec_sections_that_were_never_acceptance_criteria():
    s = build_ac_ledger(_PLAN, _ALL_ACCEPTED, spec_markdown=_SPEC_MD)["summary"]
    assert s["spec_sections_unverified"] == ["Slug rules", "Out of scope"]


def test_markdown_carries_the_caveat_on_a_green_run():
    led = build_ac_ledger(_PLAN, _ALL_ACCEPTED, spec_markdown=_SPEC_MD)
    md = render_markdown(led)
    assert "2 sections of the spec body were not represented as acceptance criteria" in md
    assert "Slug rules" in md and "Out of scope" in md


def test_triager_hands_the_ingested_spec_to_the_ledger(tmp_path):
    """The wiring: without it the caveat is computable but never computed (#855)."""
    spec_dir = tmp_path / "spec"
    (spec_dir / "context").mkdir(parents=True)
    (spec_dir / "context" / "aifactory_spec.md").write_text(_SPEC_MD)
    (spec_dir / "test_plan.json").write_text(json.dumps(_PLAN))

    summary = _write_ac_fidelity(
        spec_dir,
        [SimpleNamespace(test_id=v["test_id"], test_file="f") for v in _ALL_ACCEPTED],
        [],
        [],
    )
    assert summary["all_acs_verified"] is True
    assert summary["spec_sections_unverified"] == ["Slug rules", "Out of scope"]
    md = (spec_dir / "findings" / "ac_fidelity.md").read_text()
    assert "were not represented as acceptance criteria and were not verified" in md


def test_replan_suffix_matches_base_ac():
    plan = {"phases": [{"phase": 1, "name": "AC#1: x", "subtasks": [{"id": "root-200-unit-r1"}]}]}
    led = build_ac_ledger(plan, [{"test_id": "root-200-unit", "verdict": "accept"}])
    assert led["acceptance"][0]["status"] == "verified"  # -r1 matched its base verdict


def test_markdown_flags_incomplete_run():
    md = render_markdown(build_ac_ledger(_PLAN, _VERDICTS))
    assert "Verified 1/4" in md
    assert "not a full pass" in md
    assert "AC#3 [UNVERIFIED]" in md
    assert "no test covers this criterion" in md  # AC#4


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok:", n)
    print("ac_fidelity tests: passed")
