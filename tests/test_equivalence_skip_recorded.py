"""A skipped equivalence lane must leave a verdict in findings, not a log line (#972).

`val_block` already reported VAL-2 as `not_run` when the lane produced nothing,
so a skip never overclaimed. The defect is narrower and worse for a reader:
`findings/` was byte-identical whether the declared lane passed or never ran at
all. One WARNING line in the evaluator log is not a record — the findings file
is the artifact a human opens.

Refs TFactory#959, Factory#430.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import equivalence_lane as el  # noqa: E402
from agents import evaluator as ev  # noqa: E402
from agents import val_block as vb  # noqa: E402

_DECLARED = {"tfactory": {"equivalence": {"functions": [{"module": "a.py"}]}}}


def _spec(tmp_path: Path, contract: dict | str | None) -> Path:
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "findings").mkdir(parents=True)
    if contract is not None:
        text = contract if isinstance(contract, str) else json.dumps(contract)
        (spec / "context" / "task_contract.json").write_text(text)
    return spec


def _verdicts(spec: Path) -> list[dict]:
    path = spec / "findings" / "verdicts.json"
    return json.loads(path.read_text())["verdicts"] if path.exists() else []


def _report(spec: Path) -> dict | None:
    path = spec / "findings" / "equivalence_report.json"
    return json.loads(path.read_text()) if path.exists() else None


# ─── the lane is declared but switched off ──────────────────────────────


def test_disabled_lane_is_recorded_as_not_run(tmp_path, monkeypatch):
    """A contract declaring equivalence with the flag off leaves a record."""
    monkeypatch.delenv("TFACTORY_EQUIVALENCE_LANE", raising=False)
    spec = _spec(tmp_path, _DECLARED)

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    verdicts = _verdicts(spec)
    assert [v["lane"] for v in verdicts] == ["equivalence"]
    assert verdicts[0]["verdict"] == "not_run"
    assert "disabled" in verdicts[0]["reason"]
    report = _report(spec)
    assert report is not None and report["measured"] is False


def test_disabled_lane_does_not_fail_val2(tmp_path, monkeypatch):
    """`not_run` is an operator choice, not an acceptance failure.

    It must leave VAL-2 unproven, never "failed" — a level the run never
    attempted has no business reporting a gap.
    """
    monkeypatch.delenv("TFACTORY_EQUIVALENCE_LANE", raising=False)
    spec = _spec(tmp_path, _DECLARED)

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    block = vb.build_verification_block(_verdicts(spec))
    val2 = next(lvl for lvl in block["levels"] if lvl["level"] == "VAL-2")
    assert val2["status"] == "not_run"


def test_a_not_run_only_run_does_not_claim_val0(tmp_path, monkeypatch):
    """Recording the skip must not become its own overclaim.

    VAL-0 asserts the suite executed at all. It was derived from the mere
    presence of any verdict, so a run whose ONLY verdict is the #972 `not_run`
    record would have claimed VAL-0 passed on evidence saying nothing ran.
    """
    monkeypatch.delenv("TFACTORY_EQUIVALENCE_LANE", raising=False)
    spec = _spec(tmp_path, _DECLARED)

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    block = vb.build_verification_block(_verdicts(spec))
    val0 = next(lvl for lvl in block["levels"] if lvl["level"] == "VAL-0")
    assert val0["status"] == "not_run"


# ─── the lane is declared, enabled, and cannot run ──────────────────────


def test_unrunnable_lane_is_recorded_as_reject(tmp_path, monkeypatch):
    """No Docker daemon / no Job / missing image: fail closed and say so.

    This is the case the issue named: the lane threw for a reason that is not a
    dead harness, and the run produced no equivalence_report.json, no verdict,
    and one WARNING line.
    """
    monkeypatch.setenv("TFACTORY_EQUIVALENCE_LANE", "1")
    spec = _spec(tmp_path, _DECLARED)

    def _boom(*_a, **_k):
        raise RuntimeError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(el, "run_from_spec", _boom)

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    verdicts = _verdicts(spec)
    assert verdicts[0]["verdict"] == "reject"
    assert "Docker daemon" in verdicts[0]["reason"]
    assert _report(spec)["measured"] is False


def test_unrunnable_lane_caps_the_achieved_level(tmp_path, monkeypatch):
    """A lane that could not measure must not leave VAL-2 looking proven."""
    monkeypatch.setenv("TFACTORY_EQUIVALENCE_LANE", "1")
    spec = _spec(tmp_path, _DECLARED)
    monkeypatch.setattr(
        el, "run_from_spec", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    )

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    block = vb.build_verification_block(
        [*_verdicts(spec), {"lane": "unit", "verdict": "accept"}]
    )
    val2 = next(lvl for lvl in block["levels"] if lvl["level"] == "VAL-2")
    assert val2["status"] == "failed"
    assert block["achieved_level"] != "VAL-2"


# ─── silence stays silent where nothing was declared ────────────────────


@pytest.mark.parametrize(
    ("contract", "label"),
    [
        (None, "no contract at all"),
        ({"tfactory": {}}, "a contract declaring no equivalence block"),
        ({}, "an empty contract"),
    ],
)
def test_an_undeclared_lane_records_nothing(tmp_path, monkeypatch, contract, label):
    """Nothing was declared, so there is nothing to report.

    The record exists to distinguish "declared and did not run" from "ran and
    passed". Writing one for every run that never wanted the lane would be noise
    in every findings dir in the fleet.
    """
    monkeypatch.delenv("TFACTORY_EQUIVALENCE_LANE", raising=False)
    spec = _spec(tmp_path, contract)

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    assert _verdicts(spec) == [], label
    assert _report(spec) is None, label


def test_an_unreadable_contract_records_nothing(tmp_path, monkeypatch):
    """A contract that will not parse cannot tell us a lane was declared."""
    monkeypatch.delenv("TFACTORY_EQUIVALENCE_LANE", raising=False)
    spec = _spec(tmp_path, "{not json")

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    assert _verdicts(spec) == []
    assert _report(spec) is None


# ─── the record is appended, never clobbering what ran ──────────────────


def test_the_record_is_appended_to_existing_verdicts(tmp_path, monkeypatch):
    monkeypatch.delenv("TFACTORY_EQUIVALENCE_LANE", raising=False)
    spec = _spec(tmp_path, _DECLARED)
    (spec / "findings" / "verdicts.json").write_text(
        json.dumps({"verdicts": [{"lane": "unit", "verdict": "accept"}]})
    )

    ev._maybe_run_equivalence_lane(spec, tmp_path / "project")

    lanes = [v["lane"] for v in _verdicts(spec)]
    assert lanes == ["unit", "equivalence"]
