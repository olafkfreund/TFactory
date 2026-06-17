"""Browser-lane verdict from the Nix-Job junit: a passing UI test can be ACCEPTED.

Without this the browser lane has no real pass/fail in k3d (DockerRunner blocked),
so UI acceptance criteria are stuck flagged. Here the per-spec junit pass/fail
becomes the stability signal.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.evaluator import _browser_evidence_stability
from agents.nix_env import parse_browser_junit
from agents.stability_runner import StabilityVerdict

_JUNIT = """<testsuites tests="3" failures="1" errors="0">
<testsuite name="root-page-heading.spec.ts" tests="1" failures="0" errors="0">
<testcase name="h1" classname="root-page-heading.spec.ts"/></testsuite>
<testsuite name="ping.spec.ts" tests="1" failures="1" errors="0">
<testcase name="ping"><failure>boom</failure></testcase></testsuite>
<testsuite name="empty.spec.ts" tests="0" failures="0" errors="0"></testsuite>
</testsuites>"""


def test_parse_browser_junit(tmp_path):
    j = tmp_path / "junit.xml"
    j.write_text(_JUNIT)
    res = parse_browser_junit(j)
    assert res["root-page-heading.spec.ts"] is True   # passed
    assert res["ping.spec.ts"] is False               # failed
    assert res["empty.spec.ts"] is False              # 0 tests != passing


def test_parse_missing_junit_is_empty(tmp_path):
    assert parse_browser_junit(tmp_path / "nope.xml") == {}


def test_evidence_stability_passed(tmp_path):
    spec = tmp_path / "specs" / "x"
    (spec / "findings").mkdir(parents=True)
    (spec / "findings" / "browser_evidence.json").write_text(
        json.dumps({"root-page-heading.spec.ts": True})
    )
    st = _browser_evidence_stability(
        spec, {"files_to_create": ["tests/e2e/root-page-heading.spec.ts"]}
    )
    assert st is not None and st.verdict == StabilityVerdict.STABLE


def test_evidence_stability_failed(tmp_path):
    spec = tmp_path / "specs" / "x"
    (spec / "findings").mkdir(parents=True)
    (spec / "findings" / "browser_evidence.json").write_text(
        json.dumps({"ping.spec.ts": False})
    )
    st = _browser_evidence_stability(spec, {"files_to_create": ["tests/e2e/ping.spec.ts"]})
    assert st is not None and st.verdict == StabilityVerdict.CONSISTENT_FAIL


def test_evidence_stability_none_without_evidence(tmp_path):
    spec = tmp_path / "specs" / "x"
    (spec / "findings").mkdir(parents=True)
    # no browser_evidence.json -> None (caller falls back to the runner)
    assert _browser_evidence_stability(spec, {"files_to_create": ["tests/e2e/a.spec.ts"]}) is None
