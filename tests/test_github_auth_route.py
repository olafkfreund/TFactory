"""Extracted GitHub CLI/auth/token sub-router — #360 (god-file split).

Wiring check: the github CLI/auth/token endpoints stay mounted at the same URLs
under /api/github after extraction from routes/github.py. Skipped without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import github_auth  # noqa: E402


def test_github_auth_routes_registered():
    app = FastAPI()
    app.include_router(github_auth.router, prefix="/api/github")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    for path, method in (
        ("/api/github/cli/check", "GET"),
        ("/api/github/cli/install", "POST"),
        ("/api/github/auth/check", "GET"),
        ("/api/github/auth/start", "POST"),
        ("/api/github/auth/status", "GET"),
        ("/api/github/token", "GET"),
        ("/api/github/persist-token", "POST"),
    ):
        assert (path, method) in have, f"missing {method} {path}"
