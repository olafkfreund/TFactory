"""Extracted GitHub PR-operations sub-router — #360 (god-file split).

Wiring check: the PR endpoints stay mounted at the same URLs (under
/api/projects/{projectId}/github via projects.py) after extraction from
routes/github.py. Skipped without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import github_pr  # noqa: E402

_PREFIX = "/api/projects/{projectId}/github"


def test_pr_routes_registered_at_same_paths():
    app = FastAPI()
    app.include_router(github_pr.router, prefix=_PREFIX)
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    for path, method in (
        (f"{_PREFIX}/prs", "GET"),
        (f"{_PREFIX}/prs/{{prNumber}}/review", "POST"),
        (f"{_PREFIX}/prs/{{prNumber}}/review", "GET"),
        (f"{_PREFIX}/prs/{{prNumber}}/review", "DELETE"),
        (f"{_PREFIX}/prs/{{prNumber}}/merge", "POST"),
        (f"{_PREFIX}/prs/{{prNumber}}/comment", "POST"),
        (f"{_PREFIX}/prs/{{prNumber}}/logs", "GET"),
    ):
        assert (path, method) in have, f"missing {method} {path}"
