"""Extracted local-LLM/Ollama/OpenAI-compat settings sub-router — #360.

Wiring check: the LLM-provider endpoints stay mounted at the same URLs under
/api/settings after extraction. Skipped without FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from server.routes import settings_llm_providers  # noqa: E402


def test_llm_provider_routes_registered():
    app = FastAPI()
    app.include_router(settings_llm_providers.router, prefix="/api/settings")
    have = {
        (r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()
    }
    for path, method in (
        ("/api/settings/local-llm/detect", "GET"),
        ("/api/settings/ollama/models", "GET"),
        ("/api/settings/openai-compat/models", "GET"),
        ("/api/settings/openai-compat/test", "POST"),
        ("/api/settings/ollama/pull", "POST"),
        ("/api/settings/ollama/test", "POST"),
    ):
        assert (path, method) in have, f"missing {method} {path}"
