"""Tests for the public test-acceptance badge route (#241, epic #232).

GET /api/badges/<project>/<spec>/test-acceptance.svg — always 200 SVG.
Skipped automatically in venvs without FastAPI (see tests/conftest.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make apps/web-server importable for `server.routes.badges`.
_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.routes import badges as badge_routes  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(badge_routes.router, prefix="/api/badges")
    return TestClient(app), tmp_path


def _seed(root: Path, **status_extra):
    sd = root / "workspaces" / "demo" / "specs" / "001-login"
    (sd / "findings").mkdir(parents=True)
    status = {"status": "triaged", "verdicts_count": 4, "committed_count": 3}
    status.update(status_extra)
    (sd / "status.json").write_text(json.dumps(status))
    doc = {
        "verdicts": [{"test_id": "a", "verdict": "accept", "signals_summary": {}}],
        "confidence_summary": {"accepted_mean": 0.9, "mean": 0.7, "commit_readiness": "high"},
    }
    (sd / "findings" / "verdicts.json").write_text(json.dumps(doc))
    return sd


def test_badge_happy_path(client):
    c, root = client
    _seed(root)
    r = c.get("/api/badges/demo/001-login/test-acceptance.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "cache-control" in {k.lower() for k in r.headers}
    body = r.text
    assert body.startswith("<svg")
    assert "75%" in body  # 3/4
    assert "#4c1" in body  # high readiness → green


def test_badge_unknown_workspace_is_no_data(client):
    c, _ = client
    r = c.get("/api/badges/nope/missing/test-acceptance.svg")
    assert r.status_code == 200
    assert "no data" in r.text


def test_badge_rejects_invalid_ids(client):
    c, _ = client
    # '!' is a valid single path segment but fails the id allowlist → grey badge,
    # never a filesystem lookup.
    r = c.get("/api/badges/bad!id/x/test-acceptance.svg")
    assert r.status_code == 200
    assert "no data" in r.text
