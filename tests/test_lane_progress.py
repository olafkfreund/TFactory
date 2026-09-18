"""lane_progress derived from the plan + what is on disk (#1259).

It read ``pending`` for every lane while a run had 27 executed tests, 13
junit.xml files and 6 mutants on disk: the only writer ran once, at the end of
evaluation, from verdict lanes — so it was stale for the whole run, and stayed
stale when the verdicts carried no lane (#1258).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agents.lane_progress import derive_lane_progress


def _spec(tmp_path: Path, status: str | None, *subtasks: dict) -> Path:
    (tmp_path / "test_plan.json").write_text(
        json.dumps({"phases": [{"subtasks": list(subtasks)}]})
    )
    if status is not None:
        (tmp_path / "status.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "lane_progress": dict.fromkeys(
                        ("unit", "browser", "api", "integration", "mutation"),
                        "pending",
                    ),
                }
            )
        )
    return tmp_path


def _st(sid: str, lane: str, path: str) -> dict:
    return {"id": sid, "lane": lane, "files_to_create": [path]}


def _artifacts(spec: Path, stem: str) -> Path:
    d = spec / "findings" / "_run_artifacts" / stem
    d.mkdir(parents=True, exist_ok=True)
    (d / "junit.xml").write_text("<testsuite/>")
    (d / "coverage.xml").write_text("<coverage/>")
    return d


def _age(path: Path, seconds: int) -> None:
    """Push every file under *path* into the past."""
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    for f in files:
        t = f.stat().st_mtime - seconds
        os.utime(f, (t, t))


def test_completed_unit_lane_is_executed_before_verdicts(tmp_path):
    """The issue's exact state: unit lane finished, judge not yet run."""
    spec = _spec(tmp_path, "evaluating", _st("ac1", "unit", "tests/test_ac1.py"))
    _artifacts(spec, "test_ac1")

    assert derive_lane_progress(spec)["unit"] == "executed"


def test_evaluating_without_artifacts_is_running(tmp_path):
    spec = _spec(tmp_path, "evaluating", _st("ac1", "unit", "tests/test_ac1.py"))

    assert derive_lane_progress(spec)["unit"] == "running"


def test_not_started_is_pending(tmp_path):
    spec = _spec(tmp_path, "generating", _st("ac1", "unit", "tests/test_ac1.py"))

    assert derive_lane_progress(spec)["unit"] == "pending"


def test_verdict_without_lane_stamp_still_executed(tmp_path):
    """#1258 shape: the judge paraphrased the id and no lane was stamped."""
    spec = _spec(tmp_path, "evaluated", _st("ac1", "unit", "tests/test_ac1.py"))
    (spec / "findings").mkdir()
    (spec / "findings" / "verdicts.json").write_text(
        json.dumps(
            {
                "verdicts": [
                    {
                        "test_id": "test_ac1",
                        "test_file": "tests/test_ac1.py",
                        "signals_summary": {"stability": "stable"},
                    }
                ]
            }
        )
    )

    assert derive_lane_progress(spec)["unit"] == "executed"


def test_all_stability_error_is_error(tmp_path):
    spec = _spec(tmp_path, "evaluated", _st("b1", "browser", "tests/b1.spec.ts"))
    (spec / "findings").mkdir()
    (spec / "findings" / "verdicts.json").write_text(
        json.dumps(
            {
                "verdicts": [
                    {
                        "test_id": "b1",
                        "lane": "browser",
                        "signals_summary": {"stability": "error"},
                    }
                ]
            }
        )
    )

    assert derive_lane_progress(spec)["browser"] == "error"


def test_lane_not_in_plan_keeps_existing_value(tmp_path):
    spec = _spec(tmp_path, "evaluating", _st("ac1", "unit", "tests/test_ac1.py"))
    status = json.loads((spec / "status.json").read_text())
    status["lane_progress"]["api"] = "executed"
    (spec / "status.json").write_text(json.dumps(status))

    progress = derive_lane_progress(spec)

    assert progress["api"] == "executed"
    assert progress["mutation"] == "pending"


def test_unreadable_plan_does_not_raise(tmp_path):
    (tmp_path / "test_plan.json").write_text("{not json")
    (tmp_path / "status.json").write_text("also not json")

    assert derive_lane_progress(tmp_path) is None


def test_rerun_before_planner_ignores_old_artifacts(tmp_path):
    """A rerun resets status to pending but leaves findings/ on disk. Until
    the lanes run again, the previous run's files must not read as executed."""
    spec = _spec(tmp_path, "pending", _st("ac1", "unit", "tests/test_ac1.py"))
    _artifacts(spec, "test_ac1")

    assert derive_lane_progress(spec)["unit"] == "pending"


def test_artifacts_older_than_the_plan_are_stale(tmp_path):
    """The Planner rewrites test_plan.json every run and the Evaluator only
    reads it, so evidence older than the plan belongs to an earlier run."""
    spec = _spec(tmp_path, "evaluating", _st("ac1", "unit", "tests/test_ac1.py"))
    _age(_artifacts(spec, "test_ac1"), 600)

    assert derive_lane_progress(spec)["unit"] == "running"


def test_mutation_probe_marks_the_mutation_lane(tmp_path):
    spec = _spec(tmp_path, "evaluating", _st("m1", "mutation", "tests/test_m1.py"))
    mutants = spec / "findings" / "mutants"
    mutants.mkdir(parents=True)
    (mutants / "m1.py").write_text("x = 1")

    assert derive_lane_progress(spec)["mutation"] == "executed"
