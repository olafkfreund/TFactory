"""Tests for the real run_triager + auto-fire scaffold —
Task 8 (#9) commit 5.

Covers the full triager pipeline with the commit-2/3/4 primitives:
load verdicts.json → wrap candidates → filter rejects → dedup →
rank → render reports → git_writer (dry-run) → pr_comment (dry-run).
Side-effect helpers default to dry-run via env, so no test ever
shells out to real git/gh.

Covered:
  - Happy 3-verdict path: 1 accept + 1 flag + 1 reject →
    triage_report.{md,json} emitted with correct counts, git_writer
    dry-run argv recorded, pr_comment body written when no PR number
  - Empty verdicts → triaged_empty, empty report emitted, no
    side-effect calls
  - Missing verdicts.json → triager_failed (phase=triager_no_verdicts)
  - Malformed verdicts.json → triager_failed
  - Dedup collision recorded in report + status.json
  - source.json with pr_number → pr_comment dry-run path runs
  - source.json with branch but no pr_number → comment body written
    to disk, gh skipped
  - schedule_triager env-gate + GC anchor (carried from commit 1)
  - Forward chain from evaluator (carried from commit 1)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.triager import (
    _BG_TRIAGER_TASKS,
    run_triager,
    schedule_triager,
)


# ── autouse env pins ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")
    # Side-effect env vars must be UNSET so dry-run stays on
    monkeypatch.delenv("TFACTORY_TRIAGER_GIT_WRITE", raising=False)
    monkeypatch.delenv("TFACTORY_TRIAGER_PR_COMMENT", raising=False)


# ── Workspace helpers ──────────────────────────────────────────────────


def _make_verdicts(num: int, verdicts: list[str] | None = None) -> dict:
    """Build a verdicts.json doc with N entries.

    Each entry pairs (test_id, verdict_label, test_file). Default
    verdict is 'accept'; override via the verdicts param.
    """
    labels = verdicts or ["accept"] * num
    assert len(labels) == num
    return {
        "evaluator_version": "task7-commit5",
        "mode": "initial",
        "generated_at": "2026-05-28T00:00:00+00:00",
        "verdicts": [
            {
                "test_id": f"st{i}",
                "test_file": f"tests/test_{i}.py",
                "verdict": labels[i],
                "reasons": [f"reason for st{i}"],
                "signals_summary": {
                    "coverage_delta_pct": 1.0,
                    "stability": "stable",
                    "mutation": "killed",
                    "lint_promotion": "no findings",
                },
                "semantic_relevance": "high",
            }
            for i in range(num)
        ],
    }


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-feat"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(json.dumps({
        "task_id": "001-feat",
        "project_id": "demo",
        "spec_id": "001-feat",
        "status": "evaluated",
        "phase": "evaluator_complete",
        "verdicts_count": 3,
        "tests_evaluated": 3,
    }))
    # Source metadata — no PR number by default; tests can override.
    (d / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
        "branch": "auto-claude/test-feat",
        "base_ref": "main",
    }))
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


def _write_test_files(spec_dir: Path, count: int) -> None:
    """Write N pytest files under spec_dir/tests/ matching the
    verdicts.json test_file paths."""
    for i in range(count):
        path = spec_dir / "tests" / f"test_{i}.py"
        path.write_text(f"def test_{i}():\n    assert True\n")


# ── Happy paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_three_verdicts_mixed(
    spec_dir: Path, project_dir: Path,
) -> None:
    """1 accept + 1 flag + 1 reject → triaged, report emitted,
    git_writer dry-run argv recorded."""
    verdicts = _make_verdicts(3, ["accept", "flag", "reject"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 3)

    ok = await run_triager(spec_dir, project_dir, mode="initial")
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triaged"
    assert status["committed_count"] == 1
    assert status["flagged_count"] == 1
    assert status["rejected_count"] == 1
    assert status["dedup_collision_count"] == 0

    # Report files emitted
    report_json = json.loads(
        (spec_dir / "findings" / "triage_report.json").read_text()
    )
    assert report_json["summary"]["committed_count"] == 1
    assert report_json["summary"]["rejected_count"] == 1
    assert len(report_json["committed"]) == 1
    assert report_json["committed"][0]["test_id"] == "st0"

    report_md = (spec_dir / "findings" / "triage_report.md").read_text()
    assert "# Triage Report" in report_md
    assert "## Committed" in report_md

    # git_writer fired in dry-run mode
    gw = status["git_writer"]
    assert gw["skipped"] is False
    assert gw["dry_run"] is True
    assert gw["ok"] is True
    # 5 dry-run argvs: verify, checkout, add, commit, rev-parse HEAD
    assert len(gw["argv_log"]) == 5

    # pr_comment skipped (no pr_number in source.json) →
    # body written to disk
    pc = status["pr_comment"]
    assert pc["skipped"] is True
    assert "no PR number" in pc["reason"]
    assert (spec_dir / "findings" / "pr_comment_body.md").exists()


@pytest.mark.asyncio
async def test_pr_comment_dry_run_when_pr_number_present(
    spec_dir: Path, project_dir: Path,
) -> None:
    """source.json with pr_number → pr_comment runs in dry-run mode
    (default env)."""
    verdicts = _make_verdicts(1, ["accept"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 1)

    (spec_dir / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
        "branch": "auto-claude/test-feat",
        "pr_number": 42,
        "repo_slug": "olafkfreund/AIFactory",
    }))

    await run_triager(spec_dir, project_dir, mode="initial")

    status = json.loads((spec_dir / "status.json").read_text())
    pc = status["pr_comment"]
    assert pc["skipped"] is False
    assert pc["dry_run"] is True
    assert pc["ok"] is True
    # argv shape with repo_slug
    assert "42" in pc["argv"]
    assert "olafkfreund/AIFactory" in pc["argv"]
    assert "--body-file" in pc["argv"]
    # body_bytes > 0 (we sent the report MD)
    assert pc["body_bytes"] > 0


@pytest.mark.asyncio
async def test_dedup_collision_recorded(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Two byte-identical generated tests → one drops, collision
    reported in report + status."""
    verdicts = _make_verdicts(2, ["accept", "accept"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    # Write IDENTICAL test files for st0 and st1
    src = "def test_x():\n    assert True\n"
    (spec_dir / "tests" / "test_0.py").write_text(src)
    (spec_dir / "tests" / "test_1.py").write_text(src)

    await run_triager(spec_dir, project_dir)

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["committed_count"] == 1   # one dropped
    assert status["dedup_collision_count"] == 1

    report = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert len(report["dedup_collisions"]) == 1
    assert report["dedup_collisions"][0]["kind"] == "byte_identical"
    assert report["dedup_collisions"][0]["representative"] == "st0"


# ── Empty paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_verdicts_is_triaged_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(0)
    ))

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triaged_empty"
    assert status["committed_count"] == 0

    # Empty report files still emitted
    report = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert report["summary"]["dedup_input_count"] == 0


@pytest.mark.asyncio
async def test_all_rejects_is_triaged_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    """All verdicts are reject → triaged_empty, but rejects recorded
    in the report."""
    verdicts = _make_verdicts(2, ["reject", "reject"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 2)

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    # No survivors → triaged_empty (committed=0 + flagged=0)
    assert status["status"] == "triaged_empty"
    assert status["committed_count"] == 0
    assert status["rejected_count"] == 2

    report = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert report["summary"]["rejected_count"] == 2


# ── Failure paths ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_verdicts_is_triager_failed(
    spec_dir: Path, project_dir: Path,
) -> None:
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triager_failed"
    assert status["phase"] == "triager_no_verdicts"


@pytest.mark.asyncio
async def test_malformed_verdicts_is_triager_failed(
    spec_dir: Path, project_dir: Path,
) -> None:
    (spec_dir / "findings" / "verdicts.json").write_text("not json {")
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["phase"] == "triager_verdicts_unparseable"


@pytest.mark.asyncio
async def test_hard_failure_caught(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force an exception inside the loop → status=triager_failed."""
    from agents import triager

    real_write = triager._write_status_patch
    call_count = {"n": 0}

    def _bomb(sd, **fields):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk full")
        return real_write(sd, **fields)

    monkeypatch.setattr(triager, "_write_status_patch", _bomb)
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(1)
    ))
    _write_test_files(spec_dir, 1)
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triager_failed"


# ── Source.json edge cases ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_writer_skipped_when_no_branch(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Source.json without branch → git_writer skipped with reason."""
    (spec_dir / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
    }))
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(1)
    ))
    _write_test_files(spec_dir, 1)

    await run_triager(spec_dir, project_dir)
    status = json.loads((spec_dir / "status.json").read_text())
    gw = status["git_writer"]
    assert gw["skipped"] is True
    assert "no branch" in gw["reason"]


@pytest.mark.asyncio
async def test_test_file_missing_skipped_gracefully(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Verdicts reference a file that doesn't exist on disk →
    candidate gets empty source, git_writer skips it but the
    report still records the verdict."""
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(2)
    ))
    # Write ONLY test_0.py; test_1.py is missing
    (spec_dir / "tests" / "test_0.py").write_text(
        "def test_x(): assert True\n"
    )

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["committed_count"] == 2  # both verdicts kept (status-wise)
    # git_writer only sees test_0.py because test_1.py had no source
    gw = status["git_writer"]
    assert gw["skipped"] is False
    assert "tests/test_0.py" in gw["committed_paths"]
    assert "tests/test_1.py" not in gw["committed_paths"]


# ── Schedule + chain (carried over from commit 1) ─────────────────────


def test_schedule_disabled_returns_none(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")
    async def _run():
        return schedule_triager(spec_dir, project_dir)
    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "1")
    task = schedule_triager(spec_dir, project_dir)
    assert task is not None
    assert task in _BG_TRIAGER_TASKS
    await task
    assert task not in _BG_TRIAGER_TASKS


@pytest.mark.asyncio
async def test_evaluator_success_path_schedules_triager(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import evaluator
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "1")
    captured: dict = {}

    def _capture(sd, pd, mode="initial"):
        captured["spec_dir"] = sd
        captured["mode"] = mode
        return None

    import agents.triager as tri_mod
    monkeypatch.setattr(tri_mod, "schedule_triager", _capture)
    evaluator._advance_to_triager(spec_dir, project_dir)
    assert captured["spec_dir"] == spec_dir
    assert captured["mode"] == "initial"


def test_advance_to_triager_swallows_import_errors(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import evaluator

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def _selective_raiser(name, *args, **kwargs):
        if name == "agents.triager":
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_selective_raiser):
        evaluator._advance_to_triager(spec_dir, project_dir)
