"""Public test-acceptance badge endpoint (#241, epic #232).

Serves a shields-style SVG for a workspace so a repo README or Backstage
catalog annotation can embed TFactory's verdict:

    ![tests](https://<host>/api/badges/<project_id>/<spec_id>/test-acceptance.svg)

Reads the workspace's ``status.json`` + ``findings/verdicts.json`` (the #238/#239
confidence rollup) and renders accept-rate coloured by commit-readiness. Public
(in PUBLIC_PREFIXES) so badges render without a token — it exposes only
aggregate counts, never test content. Always returns an SVG (a grey "no data"
badge for unknown/empty workspaces) so READMEs never show a broken image.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

# Make apps/backend importable for the badge + facts helpers.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

router = APIRouter()

_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Cache for 5 min but allow stale-while-revalidate-ish refresh on the next run.
_CACHE_CONTROL = "public, max-age=300"


def _resolve_workspace_root() -> Path:
    env_val = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    return Path(env_val).expanduser() if env_val else Path.home() / ".tfactory"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _svg_response(svg: str) -> Response:
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": _CACHE_CONTROL},
    )


@router.get("/{project_id}/{spec_id}/test-acceptance.svg")
def test_acceptance_badge(project_id: str, spec_id: str) -> Response:
    """Return the acceptance badge SVG for a workspace (always 200)."""
    from agents.backstage_integration import build_facts
    from agents.badge import acceptance_badge, render_badge_svg

    # Reject path-traversal-y ids with a grey badge rather than a 4xx (keeps the
    # README image intact).
    if not (_ID_RE.match(project_id) and _ID_RE.match(spec_id)):
        return _svg_response(render_badge_svg("tests", "no data", "#9f9f9f"))

    spec_dir = _resolve_workspace_root() / "workspaces" / project_id / "specs" / spec_id
    status = _read_json(spec_dir / "status.json")
    facts = build_facts(spec_dir, status)
    return _svg_response(acceptance_badge(facts))
