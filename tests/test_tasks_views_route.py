"""Extracted task read-view sub-router — #360 (god-file split).

Wiring check: the read-view endpoints stay mounted at the same URLs after the
extraction. Skipped in venvs without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import tasks_views  # noqa: E402


def test_view_routes_registered_at_same_paths():
    app = FastAPI()
    app.include_router(tasks_views.router, prefix="/api/tasks")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    for path in (
        "/api/tasks/{task_id}/qa-report",
        "/api/tasks/{task_id}/agent-console/sse",
        "/api/tasks/{task_id}/plan-html",
    ):
        assert (path, "GET") in have, f"missing {path}"
