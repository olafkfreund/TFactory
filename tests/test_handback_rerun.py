"""Tests for the shared pipeline-rerun core (epic #182).

``rerun_pipeline`` is used by both the ``task_rerun`` MCP tool and the inbound
AIFactory completion webhook. It must reset a task's status + bump rerun_count,
and honor ``TFACTORY_AUTO_PLAN=0`` (no Planner fired in tests).
"""

from __future__ import annotations

import json

import pytest
from agents.handback.rerun import rerun_pipeline, spec_dir_for


def _seed(root, project_id="proj", spec_id="001-x", status="triaged"):
    sd = spec_dir_for(project_id, spec_id, root)
    sd.mkdir(parents=True)
    (sd / "status.json").write_text(
        json.dumps(
            {
                "status": status,
                "phase": "triager_complete",
                "lane_progress": {"unit": "complete"},
                "rerun_count": 0,
            }
        )
    )
    return sd


def test_rerun_resets_status_and_bumps_count(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")  # don't fire the Planner
    sd = _seed(tmp_path)
    result = rerun_pipeline("proj", "001-x", lane="unit", root=tmp_path)

    assert result["task_id"] == "proj:001-x"
    assert result["rerun_count"] == 1
    assert result["status"] == "pending"
    assert result["planner_scheduled"] is False  # AUTO_PLAN=0

    status = json.loads((sd / "status.json").read_text())
    assert status["status"] == "pending"
    assert status["phase"] == "created"
    assert status["rerun_count"] == 1
    assert status["lane_progress"]["unit"] == "pending"


def test_rerun_missing_status_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        rerun_pipeline("nope", "404", root=tmp_path)


def test_rerun_creates_lane_progress_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    sd = spec_dir_for("proj", "002-y", tmp_path)
    sd.mkdir(parents=True)
    (sd / "status.json").write_text(
        json.dumps({"status": "triaged"})
    )  # no lane_progress
    rerun_pipeline("proj", "002-y", lane="browser", root=tmp_path)
    status = json.loads((sd / "status.json").read_text())
    assert status["lane_progress"]["browser"] == "pending"


def test_rerun_sut_dir_prefers_worktree(tmp_path):
    """A rerun resolves to the spec's own #742 worktree when present, not the
    shared project clone whose HEAD another spec may now own."""
    from agents.handback.rerun import _rerun_sut_dir

    sd = spec_dir_for("proj", "001-x", tmp_path)
    (sd / ".worktree").mkdir(parents=True)
    assert _rerun_sut_dir(sd, "proj", tmp_path) == sd / ".worktree"


def test_rerun_sut_dir_falls_back_to_shared_clone(tmp_path):
    """No worktree (target-mode / GC'd / lost to a roll) → the shared project
    clone from projects.json — no worse than pre-#742."""
    from agents.handback.rerun import _rerun_sut_dir

    (tmp_path / "projects.json").write_text(
        json.dumps({"projects": [{"id": "proj", "root_path": str(tmp_path / "clone")}]})
    )
    sd = spec_dir_for("proj", "001-x", tmp_path)
    sd.mkdir(parents=True)  # no .worktree
    assert _rerun_sut_dir(sd, "proj", tmp_path) == tmp_path / "clone"
