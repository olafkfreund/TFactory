"""``findings/lane_runs.json`` — post-mortem output for failing runs (#1195).

The judge LLM authors ``verdicts.json``, so per-run stdout has nowhere to live
in it.  Before this file existed, diagnosing a lane failure meant re-running
the whole card at 25-45 minutes a go.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.evaluator import _RUN_TAIL_CHARS, _persist_run_output
from agents.stability_runner import StabilityResult, StabilityRun, StabilityVerdict


class _Bundle:
    """Stand-in for EvaluatorSignals — only the fields the writer reads."""

    def __init__(self, test_id: str, stability) -> None:
        self.test_id = test_id
        self.test_file = Path(f"tests/{test_id}.test.ts")
        self.stability = stability


def _result(verdict, runs):
    return StabilityResult(verdict=verdict, runs=tuple(runs))


def test_a_failing_run_lands_its_stdout_on_disk(tmp_path):
    bundle = _Bundle(
        "t1",
        _result(
            StabilityVerdict.CONSISTENT_FAIL,
            [StabilityRun(returncode=127, stdout_tail="jest: command not found")],
        ),
    )

    _persist_run_output(tmp_path, [bundle])

    data = json.loads((tmp_path / "findings" / "lane_runs.json").read_text())
    entry = data["tests"][0]
    assert entry["test_id"] == "t1"
    assert entry["runs"][0]["returncode"] == 127
    assert "jest: command not found" in entry["runs"][0]["stdout_tail"]


def test_a_secret_in_test_output_is_scrubbed(tmp_path):
    leaked = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
    bundle = _Bundle(
        "t2",
        _result(
            StabilityVerdict.CONSISTENT_FAIL,
            [StabilityRun(returncode=1, stdout_tail=f"env dump: {leaked}")],
        ),
    )

    _persist_run_output(tmp_path, [bundle])

    written = (tmp_path / "findings" / "lane_runs.json").read_text()
    assert "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY" not in written


def test_the_tail_is_bounded(tmp_path):
    bundle = _Bundle(
        "t3",
        _result(
            StabilityVerdict.CONSISTENT_FAIL,
            [StabilityRun(returncode=1, stdout_tail="x" * (_RUN_TAIL_CHARS * 3))],
        ),
    )

    _persist_run_output(tmp_path, [bundle])

    data = json.loads((tmp_path / "findings" / "lane_runs.json").read_text())
    assert len(data["tests"][0]["runs"][0]["stdout_tail"]) == _RUN_TAIL_CHARS


def test_a_passing_test_writes_nothing(tmp_path):
    bundle = _Bundle(
        "t4",
        _result(
            StabilityVerdict.STABLE,
            [StabilityRun(returncode=0, stdout_tail="3 passed")],
        ),
    )

    _persist_run_output(tmp_path, [bundle])

    assert not (tmp_path / "findings" / "lane_runs.json").exists()
