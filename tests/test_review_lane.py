"""Unit tests for the review lane (agents/review_lane.py).

The LLM session is mocked — we assert the lane's contract: it writes a status
patch, requires findings/review.json as evidence, and reports the finding count.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from agents import review_lane


def _read_status(spec: Path) -> dict:
    return json.loads((spec / "status.json").read_text())


@pytest.fixture()
def dirs(tmp_path: Path):
    spec = tmp_path / "spec"
    spec.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    return spec, proj


@pytest.mark.asyncio
async def test_review_lane_writes_findings_and_marks_reviewed(dirs, monkeypatch):
    spec, proj = dirs
    monkeypatch.setattr(
        "agents.gen_functional._resolve_client", AsyncMock(return_value=object())
    )

    async def fake_session(client, prompt, spec_dir, verbose):
        fp = Path(spec_dir) / "findings"
        fp.mkdir(parents=True, exist_ok=True)
        (fp / "review.json").write_text(json.dumps({
            "reviewer_version": "review-lane-v1",
            "findings": [
                {"axis": "correctness", "severity": "high", "file": "a.py",
                 "finding": "x", "suggestion": "y"},
                {"axis": "security", "severity": "low", "file": "b.py",
                 "finding": "z", "suggestion": "w"},
            ],
            "summary": "ok",
        }))
        return ("done", "", {})

    monkeypatch.setattr(review_lane, "_invoke_session", fake_session)

    ok = await review_lane.run_review_lane(spec, proj)
    assert ok is True
    status = _read_status(spec)
    assert status["status"] == "reviewed"
    assert status["review_findings_count"] == 2
    assert status["phase"] == "review_initial_complete"


@pytest.mark.asyncio
async def test_review_lane_clean_review_is_valid(dirs, monkeypatch):
    # An empty findings list is a valid clean review (count 0, still reviewed).
    spec, proj = dirs
    monkeypatch.setattr(
        "agents.gen_functional._resolve_client", AsyncMock(return_value=object())
    )

    async def fake_session(client, prompt, spec_dir, verbose):
        fp = Path(spec_dir) / "findings"
        fp.mkdir(parents=True, exist_ok=True)
        (fp / "review.json").write_text(json.dumps({"findings": [], "summary": "clean"}))
        return ("done", "", {})

    monkeypatch.setattr(review_lane, "_invoke_session", fake_session)
    ok = await review_lane.run_review_lane(spec, proj)
    assert ok is True
    assert _read_status(spec)["review_findings_count"] == 0


@pytest.mark.asyncio
async def test_review_lane_no_findings_file_is_failed(dirs, monkeypatch):
    # The reviewer wrote nothing — no evidence, so the lane fails (not silent pass).
    spec, proj = dirs
    monkeypatch.setattr(
        "agents.gen_functional._resolve_client", AsyncMock(return_value=object())
    )

    async def fake_session(client, prompt, spec_dir, verbose):
        return ("done", "", {})

    monkeypatch.setattr(review_lane, "_invoke_session", fake_session)
    ok = await review_lane.run_review_lane(spec, proj)
    assert ok is False
    status = _read_status(spec)
    assert status["status"] == "review_failed"
    assert "no_evidence" in status.get("review_error", "")


@pytest.mark.asyncio
async def test_review_lane_session_error_never_raises(dirs, monkeypatch):
    spec, proj = dirs
    monkeypatch.setattr(
        "agents.gen_functional._resolve_client", AsyncMock(return_value=object())
    )

    async def boom(client, prompt, spec_dir, verbose):
        raise RuntimeError("sdk down")

    monkeypatch.setattr(review_lane, "_invoke_session", boom)
    ok = await review_lane.run_review_lane(spec, proj)
    assert ok is False
    assert _read_status(spec)["status"] == "review_failed"


@pytest.mark.asyncio
async def test_schedule_review_is_opt_in_off_by_default(dirs, monkeypatch):
    # Default OFF — the trigger is a no-op unless TFACTORY_REVIEW_LANE=1.
    spec, proj = dirs
    monkeypatch.delenv("TFACTORY_REVIEW_LANE", raising=False)
    assert review_lane.schedule_review(spec, proj) is None


@pytest.mark.asyncio
async def test_schedule_review_runs_when_enabled(dirs, monkeypatch):
    spec, proj = dirs
    monkeypatch.setenv("TFACTORY_REVIEW_LANE", "1")
    called = {}

    async def fake_run(spec_dir, project_dir, mode="initial"):
        called["yes"] = True
        return True

    monkeypatch.setattr(review_lane, "run_review_lane", fake_run)
    task = review_lane.schedule_review(spec, proj)
    assert task is not None
    await task
    assert called.get("yes") is True
