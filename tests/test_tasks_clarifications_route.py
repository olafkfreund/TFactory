"""Extracted task-clarification sub-router — #360 (god-file split).

Wiring check: the clarification endpoints stay mounted at the same URLs under
/api/tasks after extraction from routes/tasks.py. Skipped without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import tasks_clarifications  # noqa: E402


def test_clarification_routes_registered():
    app = FastAPI()
    app.include_router(tasks_clarifications.router, prefix="/api/tasks")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/tasks/{task_id}/clarifications", "POST") in have
    assert ("/api/tasks/{task_id}/clarifications/answers", "POST") in have
