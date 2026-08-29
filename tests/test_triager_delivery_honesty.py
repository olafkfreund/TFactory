"""A verify that committed nothing must not report tests as committed (#1260).

Measured live: one status.json carried ``committed_count: 5`` beside
``git_writer.ok: false``, ``committed_paths: []`` and a checkout error, and
``triage_report.md`` printed a "Committed (accept) | 5" table naming all five
tests. The branch tip was unchanged — not one accepted test landed anywhere.

Every other pass-shaped defect this fleet has fixed rendered a ZERO as a pass.
This is the inverse and worse: a POSITIVE count for work that did not happen. A
zero invites suspicion; "5 committed" closes the question.

So both directions are asserted here. A ``committed_count`` that is always zero
would be the same hole facing the other way, which is why the successful write
is tested in the same module and not left to the happy-path suite.

The seam is ``tools.git_writer.write_tests_to_branch`` — git_writer captured the
failure correctly and in full; what was missing was anything reconciling it with
the count reported three fields away.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.triager import _build_completion_envelope, run_triager
from tools import git_writer as git_writer_mod
from tools.git_writer import GitWriteResult

_ACCEPTED = 5


@pytest.fixture(autouse=True)
def _no_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("PLAN", "GENERATE", "EVALUATE", "TRIAGE"):
        monkeypatch.setenv(f"TFACTORY_AUTO_{env}", "0")
    monkeypatch.delenv("TFACTORY_TRIAGER_PR_COMMENT", raising=False)
    # Live git write: the defect only exists on the path that really commits.
    monkeypatch.setenv("TFACTORY_TRIAGER_GIT_WRITE", "1")


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "040-feat"
    for sub in ("context", "tests", "findings", "logs"):
        (d / sub).mkdir(parents=True)
    (d / "status.json").write_text(
        json.dumps(
            {
                "task_id": "040-feat",
                "project_id": "demo",
                "spec_id": "040-feat",
                "status": "evaluated",
                "verdicts_count": _ACCEPTED,
            }
        )
    )
    (d / "context" / "source.json").write_text(
        json.dumps({"project_id": "demo", "branch": "aifactory/040-feat"})
    )
    (d / "findings" / "verdicts.json").write_text(
        json.dumps(
            {
                "mode": "initial",
                "verdicts": [
                    {
                        "test_id": f"st{i}",
                        "test_file": f"tests/test_{i}.py",
                        "verdict": "accept",
                        "reasons": [f"reason {i}"],
                        "signals_summary": {
                            "coverage_delta_pct": 1.0,
                            "stability": "stable",
                            "mutation": "killed",
                        },
                        "semantic_relevance": "high",
                    }
                    for i in range(_ACCEPTED)
                ],
            }
        )
    )
    for i in range(_ACCEPTED):
        (d / "tests" / f"test_{i}.py").write_text(f"def test_{i}():\n    assert True\n")
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


def _pin_write(monkeypatch: pytest.MonkeyPatch, result: GitWriteResult) -> None:
    monkeypatch.setattr(
        git_writer_mod, "write_tests_to_branch", lambda *_a, **_k: result
    )


@pytest.mark.asyncio
async def test_a_failed_write_reports_no_committed_tests(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measured run, reproduced: 5 accepted, the checkout fails, nothing lands."""
    _pin_write(
        monkeypatch,
        GitWriteResult(
            ok=False,
            dry_run=False,
            committed_paths=(),
            commit_sha="",
            error=(
                "checkout 'aifactory/040-feat' failed: fatal: 'aifactory/040-feat' "
                "is already used by worktree at '/work/workspaces/demo'"
            ),
        ),
    )
    assert await run_triager(spec_dir, project_dir, mode="initial") is True
    status = json.loads((spec_dir / "status.json").read_text())

    assert status["git_writer"]["ok"] is False, "precondition: the write failed"
    assert status["committed_count"] == 0, (
        f"nothing was committed, so committed_count must not read {_ACCEPTED}"
    )
    assert status["accepted_count"] == _ACCEPTED, (
        "what triage accepted is still worth reporting — under its own name"
    )

    envelope = _build_completion_envelope(spec_dir, status)
    assert envelope["outcome"] == "failure", (
        "a verify that delivered nothing must not read as a clean verification"
    )
    assert envelope["halt_reason"].startswith("delivery_failed:")
    assert envelope["result"]["committed_count"] == 0
    assert envelope["result"]["accepted_count"] == _ACCEPTED

    report_md = (spec_dir / "findings" / "triage_report.md").read_text()
    assert "## Committed\n" not in report_md, (
        "an unsuccessful write must not print a Committed table"
    )
    assert "| Committed (accept) | 0 |" in report_md
    assert "Delivery FAILED" in report_md
    assert "already used by worktree" in report_md, "the reason, not just the fact"
    report_json = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert report_json["summary"]["committed_count"] == 0
    assert report_json["summary"]["accepted_count"] == _ACCEPTED


@pytest.mark.asyncio
async def test_a_successful_write_still_reports_what_landed(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inverse. A field that is always zero is the same hole, mirrored."""
    _pin_write(
        monkeypatch,
        GitWriteResult(
            ok=True,
            dry_run=False,
            committed_paths=tuple(f"tests/test_{i}.py" for i in range(_ACCEPTED)),
            commit_sha="deadbeef",
            error=None,
        ),
    )
    assert await run_triager(spec_dir, project_dir, mode="initial") is True
    status = json.loads((spec_dir / "status.json").read_text())

    assert status["git_writer"]["ok"] is True
    assert status["committed_count"] == _ACCEPTED, (
        "a delivered write must still report its committed tests"
    )
    assert status["accepted_count"] == _ACCEPTED
    assert status["status"] == "triaged"

    envelope = _build_completion_envelope(spec_dir, status)
    assert envelope["outcome"] == "success"
    assert envelope.get("halt_reason") in (None, "")

    report_md = (spec_dir / "findings" / "triage_report.md").read_text()
    assert "## Committed\n" in report_md
    assert "| Committed (accept) | 5 |" in report_md
    assert "Delivery FAILED" not in report_md
