"""AC fidelity: per-criterion verified/unverified honesty (the trust lever)."""

from __future__ import annotations

from agents.ac_fidelity import build_ac_ledger, render_markdown

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
    assert s["all_verified"] is False  # must NOT claim a full pass


def test_all_verified_true_only_when_every_ac_accepted():
    v = [{"test_id": t, "test_file": "f", "verdict": "accept"}
         for t in ("root-200-unit", "h1-browser", "ping-browser", "health-unit")]
    led = build_ac_ledger(_PLAN, v)
    assert led["summary"]["all_verified"] is True
    assert led["summary"]["verified_fraction"] == "4/4"


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
