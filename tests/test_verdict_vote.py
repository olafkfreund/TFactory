"""Tests for the best-of-N judge majority vote (#649).

Unit tests for the reusable ``agents.verdict_vote.majority_vote`` helper
(3-0, 2-1, judge-crash = fail-closed deny vote, tie-break), plus an
integration test proving the one GATING site — the Evaluator judge session —
consults the vote and records the split into status.json / the completion
result summary.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from agents.verdict_vote import majority_vote


def _judge_returning(values: list[object]):
    async def _judge(i: int) -> object:
        value = values[i]
        if isinstance(value, Exception):
            raise value
        return value

    return _judge


# ── Helper unit tests ──────────────────────────────────────────────────


async def test_unanimous_3_0() -> None:
    result = await majority_vote(
        _judge_returning(["accept", "accept", "accept"]), lambda v: v
    )
    assert result.majority == "accept"
    assert result.votes == ("accept", "accept", "accept")
    assert result.dissent == ()
    assert result.split == "3-0"
    assert result.unanimous


async def test_split_2_1_records_dissent() -> None:
    result = await majority_vote(
        _judge_returning(["accept", "reject", "accept"]), lambda v: v
    )
    assert result.majority == "accept"
    assert result.dissent == (1,)
    assert result.split == "2-1"
    assert not result.unanimous


async def test_judge_crash_is_deny_vote_fail_closed() -> None:
    # Two crashes outvote one accept: the failure mode can never accept.
    result = await majority_vote(
        _judge_returning([RuntimeError("boom"), "accept", RuntimeError("boom")]),
        lambda v: v,
    )
    assert result.majority == "reject"
    assert result.votes == ("reject", "accept", "reject")
    assert result.dissent == (1,)


async def test_extractor_none_and_crash_are_deny_votes() -> None:
    def _extract(v: object) -> str | None:
        if v == "bad":
            raise ValueError("unextractable")
        if v == "empty":
            return None
        return str(v)

    result = await majority_vote(_judge_returning(["bad", "empty", "flag"]), _extract)
    assert result.votes == ("reject", "reject", "flag")
    assert result.majority == "reject"


async def test_three_way_tie_breaks_to_most_conservative() -> None:
    result = await majority_vote(
        _judge_returning(["accept", "flag", "reject"]), lambda v: v
    )
    assert result.majority == "reject"
    assert result.split == "1-2"


async def test_single_call_passthrough() -> None:
    result = await majority_vote(_judge_returning(["flag"]), lambda v: v, n=1)
    assert result.majority == "flag"
    assert result.split == "1-0"


# ── Integration: the gating site (Evaluator judge session) votes ───────
# Mirrors tests/test_evaluator.py's seam mocking: patch the two SDK seams and
# the runner seam, then vary the verdict per session call.


def _make_test_plan() -> dict:
    return {
        "feature": "x",
        "workflow_type": "feature",
        "services_involved": [],
        "phases": [
            {
                "phase": 1,
                "name": "main",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "st0",
                        "description": "Subtask 0",
                        "status": "completed",
                        "lane": "functional",
                        "target": "app/m0.py::f0",
                        "rationale": "AC#1",
                        "files_to_create": ["tests/test_0.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_0.py",
                        },
                    }
                ],
                "parallel_safe": False,
            }
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    }


def _verdict_doc(verdict: str) -> dict:
    return {
        "evaluator_version": "task7-commit5",
        "mode": "initial",
        "verdicts": [
            {
                "test_id": "st0",
                "test_file": "tests/test_st0.py",
                "verdict": verdict,
                "reasons": [f"judge said {verdict}"],
                "signals_summary": {
                    "coverage_delta_pct": 0.0,
                    "coverage_new_lines": 0,
                    "stability": "stable",
                    "mutation": "killed",
                    "lint_promotion": "no findings",
                },
                "semantic_relevance": "high",
                "semantic_notes": "matches rationale",
            }
        ],
        "generated_at": "2026-07-11T00:00:00+00:00",
    }


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-feat"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(
        json.dumps(
            {
                "task_id": "001-feat",
                "project_id": "demo",
                "spec_id": "001-feat",
                "status": "generated",
                "phase": "gen_functional_complete",
                "tests_generated": 1,
            }
        )
    )
    (d / "test_plan.json").write_text(json.dumps(_make_test_plan()))
    test_file = d / "tests" / "test_0.py"
    test_file.write_text(
        textwrap.dedent('''
        """Test file."""
        def test_x():
            assert 1 == 1
    ''').lstrip()
    )
    return d


@pytest.fixture
def _evaluator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")
    monkeypatch.delenv("TFACTORY_VERDICT_VOTES", raising=False)  # default = 3

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _resolve_runner(_spec_dir: Path, _project_dir: Path):
        return lambda *_a, **_kw: _FakeResult()

    monkeypatch.setattr("agents.evaluator._resolve_runner_fn", _resolve_runner)


def _install_session_mock(
    monkeypatch: pytest.MonkeyPatch, per_call_verdicts: list[object]
) -> list[int]:
    """Each session call writes the next verdict doc (or raises)."""

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    calls: list[int] = []

    async def _resolve(*_a, **_kw):
        return _CM()

    async def _invoke(_client, _prompt, spec_dir_arg: Path, _verbose):
        i = len(calls)
        calls.append(i)
        action = per_call_verdicts[i]
        if isinstance(action, Exception):
            raise action
        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps(_verdict_doc(str(action)), indent=2)
        )
        return "complete", "ok", {}

    monkeypatch.setattr("agents.evaluator._resolve_evaluator_client", _resolve)
    monkeypatch.setattr("agents.evaluator._invoke_session", _invoke)
    return calls


@pytest.mark.usefixtures("_evaluator_env")
async def test_gating_site_consults_the_vote(
    spec_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2 accepts + 1 reject → majority accept, split recorded end to end."""
    from agents.evaluator import run_evaluator

    calls = _install_session_mock(monkeypatch, ["accept", "reject", "accept"])
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    ok = await run_evaluator(spec_dir, project_dir, mode="initial")
    assert ok is True
    assert len(calls) == 3  # best-of-3 is the default

    doc = json.loads((spec_dir / "findings" / "verdicts.json").read_text())
    (entry,) = doc["verdicts"]
    assert entry["verdict"] == "accept"
    assert entry["vote"]["split"] == "2-1"
    assert entry["vote"]["votes"] == ["accept", "reject", "accept"]
    assert entry["vote"]["dissent"][0]["call"] == 1
    assert entry["vote"]["dissent"][0]["verdict"] == "reject"
    assert doc["verdict_vote"]["splits"] == {"2-1": 1}
    assert doc["verdict_vote"]["split_rate"] == 1.0
    # Per-call audit trail is kept.
    assert (spec_dir / "findings" / "verdicts.vote0.json").exists()

    # The split summary rides on status.json into the completion envelope.
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluated"
    assert status["verdict_vote"]["calls"] == 3
    from agents.triager import _completion_result_summary

    assert "verdict_vote" in _completion_result_summary(status)


@pytest.mark.usefixtures("_evaluator_env")
async def test_gating_site_judge_crash_is_deny_vote(
    spec_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 accept + 2 crashed sessions → reject wins fail-closed, run completes."""
    from agents.evaluator import run_evaluator

    _install_session_mock(
        monkeypatch, ["accept", RuntimeError("boom"), RuntimeError("boom")]
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    ok = await run_evaluator(spec_dir, project_dir, mode="initial")
    assert ok is True

    doc = json.loads((spec_dir / "findings" / "verdicts.json").read_text())
    (entry,) = doc["verdicts"]
    assert entry["verdict"] == "reject"
    assert entry["vote"]["votes"] == ["accept", "reject", "reject"]
    assert doc["verdict_vote"]["failed_calls"] == 2


@pytest.mark.usefixtures("_evaluator_env")
async def test_gating_site_all_crashes_is_evaluator_failed(
    spec_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents.evaluator import run_evaluator

    _install_session_mock(
        monkeypatch,
        [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")],
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    ok = await run_evaluator(spec_dir, project_dir, mode="initial")
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "evaluator_failed"
    assert status["phase"] == "evaluator_session_error"
    assert "boom" in status["evaluator_error"]
