"""
Regression tests for the restored ``qa_loop`` module (#226 / #227).

Covers:
- the import guard (qa_loop + the whole cli build-CLI chain must import),
- the pure QA-state readers (is_qa_approved / should_run_qa / print_qa_status),
- the inlined ``get_specs_dir`` + reconstructed ``cli.spec_commands``,
- the reviewer→fixer orchestration in ``run_qa_validation_loop`` with the
  SDK seams mocked (the suite never touches a real LLM).
"""

import asyncio
import json
from pathlib import Path

import pytest
import qa_loop

# ─── fixtures ───────────────────────────────────────────────────────────


def _write_plan(spec_dir: Path, *, total=2, completed=2, qa_signoff=None) -> Path:
    """Write a minimal test_plan.json with N subtasks and optional sign-off."""
    subtasks = [
        {"id": f"st-{i}", "status": "completed" if i < completed else "pending"}
        for i in range(total)
    ]
    plan = {"phases": [{"subtasks": subtasks}]}
    if qa_signoff is not None:
        plan["qa_signoff"] = qa_signoff
    plan_file = spec_dir / "test_plan.json"
    plan_file.write_text(json.dumps(plan))
    return plan_file


@pytest.fixture
def spec_dir(tmp_path) -> Path:
    d = tmp_path / "001-feature"
    d.mkdir()
    return d


# ─── import guard (the core #226 / #227 regression) ─────────────────────


def test_import_guard():
    """qa_loop and the full build-CLI import chain must import cleanly."""
    import cli.build_commands  # noqa: F401
    import cli.main  # noqa: F401
    import cli.qa_commands  # noqa: F401
    import cli.spec_commands  # noqa: F401

    for name in (
        "is_qa_approved",
        "should_run_qa",
        "print_qa_status",
        "run_qa_validation_loop",
    ):
        assert hasattr(qa_loop, name), f"qa_loop missing {name}"
    assert asyncio.iscoroutinefunction(qa_loop.run_qa_validation_loop)


# ─── QA-state readers ───────────────────────────────────────────────────


def test_is_qa_approved_true(spec_dir):
    _write_plan(spec_dir, qa_signoff={"status": "approved"})
    assert qa_loop.is_qa_approved(spec_dir) is True


def test_is_qa_approved_rejected_or_missing(spec_dir):
    _write_plan(spec_dir, qa_signoff={"status": "rejected"})
    assert qa_loop.is_qa_approved(spec_dir) is False
    # No plan at all → not approved, no crash.
    assert qa_loop.is_qa_approved(spec_dir / "nope") is False


def test_should_run_qa_complete_unapproved(spec_dir):
    _write_plan(spec_dir, total=2, completed=2)  # complete, no sign-off
    assert qa_loop.should_run_qa(spec_dir) is True


def test_should_run_qa_false_when_approved(spec_dir):
    _write_plan(spec_dir, total=2, completed=2, qa_signoff={"status": "approved"})
    assert qa_loop.should_run_qa(spec_dir) is False


def test_should_run_qa_false_when_incomplete(spec_dir):
    _write_plan(spec_dir, total=2, completed=1)  # build not done
    assert qa_loop.should_run_qa(spec_dir) is False


def test_print_qa_status_no_signoff(spec_dir, capsys):
    _write_plan(spec_dir)
    qa_loop.print_qa_status(spec_dir)
    assert "No QA sign-off" in capsys.readouterr().out


def test_print_qa_status_approved(spec_dir, capsys):
    _write_plan(spec_dir, qa_signoff={"status": "approved", "qa_session": 1})
    qa_loop.print_qa_status(spec_dir)
    assert "approved" in capsys.readouterr().out.lower()


# ─── inlined get_specs_dir + reconstructed spec_commands ────────────────


def test_get_specs_dir():
    from cli.utils import get_specs_dir

    assert get_specs_dir(Path("/proj")) == Path("/proj/.tfactory/specs")


def test_print_specs_list(tmp_path, capsys):
    from cli.spec_commands import list_specs, print_specs_list

    specs = tmp_path / ".tfactory" / "specs"
    (specs / "001-alpha").mkdir(parents=True)
    (specs / "001-alpha" / "spec.md").write_text("# alpha")
    _write_plan(specs / "001-alpha", qa_signoff={"status": "approved"})

    assert [p.name for p in list_specs(tmp_path)] == ["001-alpha"]
    print_specs_list(tmp_path)
    out = capsys.readouterr().out
    assert "001-alpha" in out


def test_print_specs_list_empty(tmp_path, capsys):
    from cli.spec_commands import print_specs_list

    print_specs_list(tmp_path)
    assert "No specs found" in capsys.readouterr().out


# ─── run_qa_validation_loop orchestration (seams mocked) ────────────────


def _signoff_writer(spec_dir: Path, status: str):
    """Build an async reviewer/fixer stub that records a qa_signoff."""

    async def _stub(project_dir, sd, model, verbose):
        plan_file = Path(sd) / "test_plan.json"
        plan = json.loads(plan_file.read_text())
        plan["qa_signoff"] = {"status": status}
        plan_file.write_text(json.dumps(plan))
        return "complete", "", {}

    return _stub


def test_loop_approves_first_round(spec_dir, monkeypatch):
    _write_plan(spec_dir)
    monkeypatch.setattr(
        qa_loop, "_run_qa_reviewer", _signoff_writer(spec_dir, "approved")
    )
    # Fixer must never run on a first-round approval.
    monkeypatch.setattr(
        qa_loop, "_run_qa_fixer", _signoff_writer(spec_dir, "should-not-run")
    )
    approved = asyncio.run(
        qa_loop.run_qa_validation_loop(
            project_dir=spec_dir, spec_dir=spec_dir, model="sonnet"
        )
    )
    assert approved is True


def test_loop_reject_then_fix_then_approve(spec_dir, monkeypatch):
    _write_plan(spec_dir)
    calls = {"review": 0}

    async def reviewer(project_dir, sd, model, verbose):
        calls["review"] += 1
        # Reject on round 1, approve on round 2 (after the fixer runs).
        status = "approved" if calls["review"] >= 2 else "rejected"
        plan_file = Path(sd) / "test_plan.json"
        plan = json.loads(plan_file.read_text())
        plan["qa_signoff"] = {"status": status}
        plan_file.write_text(json.dumps(plan))
        return "complete", "", {}

    fixer_runs = {"n": 0}

    async def fixer(project_dir, sd, model, verbose):
        fixer_runs["n"] += 1
        return "complete", "", {}

    monkeypatch.setattr(qa_loop, "_run_qa_reviewer", reviewer)
    monkeypatch.setattr(qa_loop, "_run_qa_fixer", fixer)

    approved = asyncio.run(
        qa_loop.run_qa_validation_loop(
            project_dir=spec_dir, spec_dir=spec_dir, model="sonnet"
        )
    )
    assert approved is True
    assert calls["review"] == 2
    assert fixer_runs["n"] == 1


def test_loop_never_approves_returns_false(spec_dir, monkeypatch):
    _write_plan(spec_dir)
    monkeypatch.setattr(
        qa_loop, "_run_qa_reviewer", _signoff_writer(spec_dir, "rejected")
    )
    monkeypatch.setattr(qa_loop, "_run_qa_fixer", _signoff_writer(spec_dir, "rejected"))
    approved = asyncio.run(
        qa_loop.run_qa_validation_loop(
            project_dir=spec_dir, spec_dir=spec_dir, model="sonnet"
        )
    )
    assert approved is False


def test_loop_reviewer_error_bails(spec_dir, monkeypatch):
    _write_plan(spec_dir)

    async def erroring(project_dir, sd, model, verbose):
        return "error", "", {"message": "boom"}

    monkeypatch.setattr(qa_loop, "_run_qa_reviewer", erroring)
    approved = asyncio.run(
        qa_loop.run_qa_validation_loop(
            project_dir=spec_dir, spec_dir=spec_dir, model="sonnet"
        )
    )
    assert approved is False
