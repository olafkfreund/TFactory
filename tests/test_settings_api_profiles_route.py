"""Extracted api-profile settings sub-router — #360 (god-file split).

Wiring check: the api-profile endpoints stay mounted at the same URLs under
/api/settings after extraction. Skipped without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import settings_api_profiles  # noqa: E402


def test_api_profile_routes_registered():
    app = FastAPI()
    app.include_router(settings_api_profiles.router, prefix="/api/settings")
    paths = {r.path for r in app.routes}
    for p in (
        "/api/settings/api-profiles",
        "/api/settings/api-profiles/{profile_id}",
        "/api/settings/api-profiles/active",
        "/api/settings/api-profiles/test",
        "/api/settings/api-profiles/discover-models",
    ):
        assert p in paths, f"missing {p}"
