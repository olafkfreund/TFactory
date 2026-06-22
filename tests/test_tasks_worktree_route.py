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
