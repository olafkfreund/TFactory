"""A build phase left ``active`` by a dead process must not pin the task at
``in_progress`` forever (#1368). Startup closes such phases as failed; the
recover route resets them to pending so the plan status shows through.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.routes import execution  # noqa: E402
from server.routes import tasks as tasks_mod  # noqa: E402
from server.routes.tasks import (  # noqa: E402
    close_active_phases,
    load_spec_metadata,
    reconcile_interrupted_builds,
)


def _logs(spec_dir: Path, status: str = "active") -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "task_logs.json").write_text(
        json.dumps({"phases": {"coding": {"status": status}}})
    )


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    main = tmp_path / ".tfactory" / "specs" / "001-x"
    wt = tmp_path / ".tfactory/worktrees/tasks/001-x/.tfactory/specs/001-x"
    _logs(main)
    _logs(wt)
    return tmp_path, main, wt


def _phase(spec_dir: Path) -> dict:
    return json.loads((spec_dir / "task_logs.json").read_text())["phases"]["coding"]


def test_close_active_phases_fails_phase_and_task_needs_review(tmp_path: Path) -> None:
    _logs(tmp_path)
    assert load_spec_metadata(tmp_path)["status"] == "in_progress"

    assert close_active_phases(tmp_path, "failed") == ["coding"]

    meta = load_spec_metadata(tmp_path)
    assert meta["status"] == "human_review"
    assert meta["reviewReason"] == "errors"
    assert _phase(tmp_path)["interrupted"] is True


def test_close_active_phases_noop_and_malformed(tmp_path: Path) -> None:
    _logs(tmp_path, status="completed")
    before = (tmp_path / "task_logs.json").read_bytes()
    assert close_active_phases(tmp_path, "failed") == []
    assert (tmp_path / "task_logs.json").read_bytes() == before

    (tmp_path / "task_logs.json").write_text("{not json")
    assert close_active_phases(tmp_path, "failed") == []


def test_reconcile_closes_main_and_worktree_copy(tmp_path: Path, monkeypatch) -> None:
    project, main, wt = _project(tmp_path)
    monkeypatch.setattr(
        tasks_mod, "load_projects", lambda: {"p": {"path": str(project)}}
    )

    assert reconcile_interrupted_builds() == [("001-x", ["coding"])]
    assert _phase(main)["status"] == "failed"
    assert _phase(wt)["status"] == "failed"


def test_recover_resets_active_phase_so_backlog_shows(
    tmp_path: Path, monkeypatch
) -> None:
    project, main, _wt = _project(tmp_path)
    projects = {"p": {"path": str(project)}}
    monkeypatch.setattr(execution, "load_projects", lambda: projects)
    monkeypatch.setattr(
        execution, "get_agent_service", lambda: SimpleNamespace(running_tasks={})
    )

    async def _noop(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(execution, "emit_task_status", _noop)
    assert load_spec_metadata(main)["status"] == "in_progress"

    asyncio.run(execution.recover_task("p:001-x"))

    assert _phase(main)["status"] == "pending"
    assert load_spec_metadata(main)["status"] == "backlog"


def test_recover_after_restart_reopens_the_interrupted_phase(
    tmp_path: Path, monkeypatch
) -> None:
    # Restart first (phase -> failed, interrupted), then the user recovers.
    project, main, _wt = _project(tmp_path)
    projects = {"p": {"path": str(project)}}
    monkeypatch.setattr(tasks_mod, "load_projects", lambda: projects)
    monkeypatch.setattr(execution, "load_projects", lambda: projects)
    monkeypatch.setattr(
        execution, "get_agent_service", lambda: SimpleNamespace(running_tasks={})
    )

    async def _noop(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(execution, "emit_task_status", _noop)
    reconcile_interrupted_builds()
    assert load_spec_metadata(main)["status"] == "human_review"
    assert reconcile_interrupted_builds() == []  # idempotent

    asyncio.run(execution.recover_task("p:001-x"))

    assert "interrupted" not in _phase(main)
    assert load_spec_metadata(main)["status"] == "backlog"
