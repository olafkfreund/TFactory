"""Tests for PFactory target run orchestration (#197, epic #193).

Covers seeding the spec workspace from the oracle, the report-back wiring tied
to plan_id + originating issue, the dry-run-by-default no-auto-push policy, and
the injectable schedule seam — all without an LLM/Docker (schedule is mocked).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from integrations.pfactory import (
    Citation,
    PFactoryOracle,
    build_oracle,
    pickup_and_run,
    run_target,
    spec_markdown_from_oracle,
)


def _oracle(plan_id="001-orders", priority="p1") -> PFactoryOracle:
    return PFactoryOracle(
        plan_id=plan_id,
        plan_type="infra-change",
        category="infra",
        priority=priority,
        horizon={"p0": "now", "p1": "next"}.get(priority, "later"),
        risk="medium",
        access_verified=True,
        taxonomy_version="v1",
        citations=(Citation(why="needs auth", uri="https://owasp.org/x", source="owasp"),),
        acceptance_criteria=("AC#1: requires auth", "AC#2: returns 401 when missing"),
    )


def _capture_schedule():
    calls: list[tuple] = []

    def _schedule(spec_dir: Path, project_dir: Path) -> None:
        calls.append((spec_dir, project_dir))

    return _schedule, calls


# ─── spec rendering from the oracle ─────────────────────────────────────


def test_spec_markdown_from_oracle_has_ac_markers_and_citations() -> None:
    md = spec_markdown_from_oracle(_oracle(), title="Orders auth")
    assert "# Orders auth" in md
    # AC#N markers (no doubled prefix from the already-prefixed criteria text)
    assert "**AC#1:** requires auth" in md
    assert "**AC#2:** returns 401 when missing" in md
    assert "AC#1: AC#1" not in md  # not doubled
    # citation rides in the description
    assert "needs auth" in md and "owasp" in md


# ─── workspace seeding + report-back wiring ─────────────────────────────


def test_run_target_seeds_workspace_tied_to_plan_id(tmp_path: Path) -> None:
    schedule, calls = _capture_schedule()
    target = {"issue_number": 412, "title": "Test orders auth"}
    handle = run_target(
        target,
        _oracle(plan_id="001-orders"),
        project_id="acme",
        project_dir=tmp_path / "repo",
        repo="acme/orders",
        branch="feat/auth",
        workspace_root=tmp_path / "ws",
        schedule=schedule,
    )
    assert handle.plan_id == "001-orders"
    assert handle.spec_dir.parts[-2:] == ("specs", "001-orders")
    assert (handle.spec_dir / "context" / "aifactory_spec.md").exists()

    source = json.loads((handle.spec_dir / "context" / "source.json").read_text())
    assert source["plan_id"] == "001-orders"
    assert source["issue_number"] == 412  # spine correlation key for report-back
    assert source["pfactory"] is True
    assert source["horizon"] == "next" and source["repo"] == "acme/orders"

    status = json.loads((handle.spec_dir / "status.json").read_text())
    assert status["status"] == "pending" and status["spec_id"] == "001-orders"

    # pipeline scheduled with the seeded spec dir + project dir
    assert calls == [(handle.spec_dir, tmp_path / "repo")]
    assert handle.scheduled is True


def test_plan_id_fallback_when_absent(tmp_path: Path) -> None:
    schedule, _ = _capture_schedule()
    oracle = _oracle(plan_id=None)
    handle = run_target(
        {"issue_number": 77},
        oracle,
        project_id="acme",
        project_dir=tmp_path,
        workspace_root=tmp_path / "ws",
        schedule=schedule,
    )
    assert handle.plan_id == "pf-77"


# ─── no automatic pushes (dry-run by default) ───────────────────────────


def test_dry_run_default_does_not_enable_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TFACTORY_TRIAGER_GIT_WRITE", raising=False)
    monkeypatch.delenv("TFACTORY_TRIAGER_PR_COMMENT", raising=False)
    schedule, _ = _capture_schedule()
    handle = run_target(
        {"issue_number": 1},
        _oracle(),
        project_id="acme",
        project_dir=tmp_path,
        workspace_root=tmp_path / "ws",
        schedule=schedule,
    )
    assert handle.dry_run is True
    import os

    # We must NOT have flipped the side-effect flags on.
    assert os.environ.get("TFACTORY_TRIAGER_GIT_WRITE") is None
    assert os.environ.get("TFACTORY_TRIAGER_PR_COMMENT") is None


def test_opt_in_enables_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TFACTORY_TRIAGER_GIT_WRITE", raising=False)
    monkeypatch.delenv("TFACTORY_TRIAGER_PR_COMMENT", raising=False)
    schedule, _ = _capture_schedule()
    run_target(
        {"issue_number": 1},
        _oracle(),
        project_id="acme",
        project_dir=tmp_path,
        workspace_root=tmp_path / "ws",
        schedule=schedule,
        dry_run=False,
    )
    import os

    assert os.environ["TFACTORY_TRIAGER_GIT_WRITE"] == "1"
    assert os.environ["TFACTORY_TRIAGER_PR_COMMENT"] == "1"


# ─── pickup_and_run end-to-end (recognition → run) ──────────────────────


def test_pickup_and_run_governed_issue(tmp_path: Path) -> None:
    schedule, calls = _capture_schedule()
    issue = {
        "number": 412,
        "title": "Test orders auth",
        "body": "## Acceptance Criteria\n- AC#1: requires auth\n\n"
        "<!-- pfactory:meta\nplan_id: 001-orders\npriority: p0\ntaxonomy: v1\n-->",
        "labels": ["pfactory", "handoff:tfactory", "type:testing"],
    }
    handle = pickup_and_run(
        issue,
        project_id="acme",
        project_dir=tmp_path / "repo",
        workspace_root=tmp_path / "ws",
        schedule=schedule,
    )
    assert handle is not None
    assert handle.plan_id == "001-orders" and handle.scheduled is True
    spec = (handle.spec_dir / "context" / "aifactory_spec.md").read_text()
    assert "requires auth" in spec
    assert len(calls) == 1


def test_pickup_and_run_non_target_returns_none(tmp_path: Path) -> None:
    schedule, calls = _capture_schedule()
    issue = {"number": 1, "title": "bug", "body": "", "labels": ["bug"]}
    handle = pickup_and_run(
        issue,
        project_id="acme",
        project_dir=tmp_path,
        workspace_root=tmp_path / "ws",
        schedule=schedule,
    )
    assert handle is None
    assert calls == []  # nothing scheduled
