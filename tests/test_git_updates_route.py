"""Extracted source-update sub-router — #360 (god-file split).

Wiring check: the self-update endpoints stay mounted at the same URLs under
/api/updates after extraction from routes/git.py. Skipped without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import git_updates  # noqa: E402


def test_update_routes_registered():
    app = FastAPI()
    app.include_router(git_updates.router, prefix="/api/updates")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    for path, method in (
        ("/api/updates/source/check", "GET"),
        ("/api/updates/source/download", "POST"),
        ("/api/updates/source/version", "GET"),
    ):
        assert (path, method) in have, f"missing {method} {path}"
