"""Route-registration check for the extracted worktree sub-router — #360.

The worktree handlers drive real git operations (no in-process behavioural
test), so this asserts the **wiring** is unchanged: every worktree path is
still mounted at the exact same URL under the /api/tasks prefix after the
extraction from routes/tasks.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import tasks_worktree  # noqa: E402

_EXPECTED = {
    ("/api/tasks/{task_id}/worktree/merge-preview", "GET"),
    ("/api/tasks/{task_id}/worktree/resolve-conflicts", "POST"),
    ("/api/tasks/{task_id}/worktree/resolve-uncommitted", "POST"),
    ("/api/tasks/{task_id}/worktree/resolve-git-merge", "POST"),
    ("/api/tasks/{task_id}/worktree/abort-merge", "POST"),
    ("/api/tasks/{task_id}/worktree/create-pr", "POST"),
    ("/api/tasks/{task_id}/worktree/merge", "POST"),
    ("/api/tasks/{task_id}/worktree/status", "GET"),
    ("/api/tasks/{task_id}/worktree/diff", "GET"),
    ("/api/tasks/{task_id}/worktree/discard", "POST"),
}


def test_all_worktree_routes_registered_at_same_paths():
    app = FastAPI()
    app.include_router(tasks_worktree.router, prefix="/api/tasks")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    missing = {(p, m) for (p, m) in _EXPECTED if (p, m) not in have}
    assert not missing, f"worktree routes not registered: {missing}"


def _projects(tmp_path, monkeypatch):
    import json

    proj = tmp_path / "p1"
    (proj / ".tfactory" / "specs" / "s1").mkdir(parents=True)
    (proj / ".tfactory" / "worktrees" / "tasks" / "s1").mkdir(parents=True)
    (tmp_path / "projects.json").write_text(json.dumps({"p1": {"path": str(proj)}}))
    monkeypatch.setattr(tasks_worktree, "get_data_dir", lambda: tmp_path)


def test_status_and_diff_need_exact_project(tmp_path, monkeypatch):
    """#1466: a bare id or unknown prefix must not find p1's spec by scanning."""
    import asyncio

    import pytest
    from fastapi import HTTPException

    _projects(tmp_path, monkeypatch)
    for handler in (
        tasks_worktree.get_worktree_status,
        tasks_worktree.get_worktree_diff,
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(handler("s1"))
        assert exc.value.status_code == 400
    status = asyncio.run(tasks_worktree.get_worktree_status("nope:s1"))
    assert status == {"success": True, "data": {"exists": False}}
    diff = asyncio.run(tasks_worktree.get_worktree_diff("nope:s1"))
    assert diff == {"success": False, "error": "Project not found for task nope:s1"}
