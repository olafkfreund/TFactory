"""Tests for the WS2 spec-ingestion route — POST /api/specs/ingest.

Mounts only the specs router with a TestClient; the seams are monkeypatched so
nothing touches a real workspace or the Planner:
  - project resolution via the web-server store ``server.routes.projects.load_projects``
    (the route switched off the backend file store in #517 — it resolves by id OR name);
  - ``_ensure_project_clone`` (the #539 clone self-heal — covered on its own in
    ``tests/test_specs_ingest_ensure_clone.py``) is stubbed to a passthrough;
  - the backend seam ``create_spec_ingest_workspace``.
Covers happy path + the 404/400/409/422 error mappings.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
for _p in (_WEB_SERVER, _BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import agents.tools_pkg.tools.task_control as tc  # noqa: E402
from server.routes import projects as projects_store  # noqa: E402
from server.routes import specs  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(specs.router)
    return TestClient(app)


@pytest.fixture
def known_project(monkeypatch):
    # The route resolves projects from the web-server store as {id: data} (#517).
    monkeypatch.setattr(
        projects_store,
        "load_projects",
        lambda *a, **k: {"proj": {"path": "/tmp/p", "name": "proj"}},
    )

    # Bypass the clone self-heal (#539) — exercised directly elsewhere — so these
    # tests stay focused on resolution + error mapping. Passthrough the path.
    async def _passthrough(entry, resolved_id, *, source_branch):
        return entry.get("path") or entry.get("root_path") or "."

    monkeypatch.setattr(specs, "_ensure_project_clone", _passthrough)


def _body(**kw):
    base = {
        "project_id": "proj",
        "spec_id": "s1",
        "spec_text": "# T\n## Acceptance Criteria\n- a",
    }
    base.update(kw)
    return base


def test_ingest_happy(client, known_project, monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {
            "spec_dir": "/tmp/p/ws",
            "source_format": "markdown",
            "ac_count": 1,
            "planner_scheduled": False,
            "warnings": [],
        }

    monkeypatch.setattr(tc, "create_spec_ingest_workspace", fake_create)
    r = client.post("/api/specs/ingest", json=_body(target_paths=["src/x.py"]))
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "s1" and body["project_id"] == "proj"
    assert body["source_format"] == "markdown" and body["ac_count"] == 1
    # request fields threaded through to the seam
    assert captured["project_root"] == "/tmp/p"
    assert captured["target_paths"] == ["src/x.py"]


def _capture_create(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {
            "spec_dir": "/tmp/p/ws",
            "source_format": "markdown",
            "ac_count": 1,
            "planner_scheduled": False,
            "warnings": [],
        }

    monkeypatch.setattr(tc, "create_spec_ingest_workspace", fake_create)
    return captured


def test_ingest_payload_tenant_stamped(client, known_project, monkeypatch):
    """An explicit payload tenant (AIFactory stamp) reaches the seam (#683)."""
    captured = _capture_create(monkeypatch)
    r = client.post("/api/specs/ingest", json=_body(tenant="acme"))
    assert r.status_code == 200
    assert captured["tenant"] == "acme"


def test_ingest_header_tenant_multi_tenant_on(client, known_project, monkeypatch):
    """Multi-tenant on: X-Tenant-Id resolves the tenant when the payload
    carries none (#683)."""
    monkeypatch.setenv("TFACTORY_MULTI_TENANT", "true")
    captured = _capture_create(monkeypatch)
    r = client.post("/api/specs/ingest", json=_body(), headers={"X-Tenant-Id": "acme"})
    assert r.status_code == 200
    assert captured["tenant"] == "acme"


def test_ingest_header_ignored_when_flag_off(client, known_project, monkeypatch):
    """Multi-tenant off (default): the header is ignored, tenant='default'."""
    monkeypatch.delenv("TFACTORY_MULTI_TENANT", raising=False)
    captured = _capture_create(monkeypatch)
    r = client.post("/api/specs/ingest", json=_body(), headers={"X-Tenant-Id": "acme"})
    assert r.status_code == 200
    assert captured["tenant"] == "default"


def test_unknown_project_404(client, monkeypatch):
    monkeypatch.setattr(projects_store, "load_projects", lambda *a, **k: {})
    r = client.post("/api/specs/ingest", json=_body())
    assert r.status_code == 404
    assert "unknown project_id" in r.json()["detail"]


def test_value_error_maps_to_400(client, known_project, monkeypatch):
    def boom(**kwargs):
        raise ValueError("spec has no acceptance criteria")

    monkeypatch.setattr(tc, "create_spec_ingest_workspace", boom)
    r = client.post("/api/specs/ingest", json=_body())
    assert r.status_code == 400
    assert "no acceptance criteria" in r.json()["detail"]


def test_existing_dir_maps_to_409(client, known_project, monkeypatch):
    def boom(**kwargs):
        raise FileExistsError("spec_dir already exists: /tmp/p/ws")

    monkeypatch.setattr(tc, "create_spec_ingest_workspace", boom)
    r = client.post("/api/specs/ingest", json=_body())
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_missing_spec_text_422(client, known_project):
    r = client.post("/api/specs/ingest", json={"project_id": "proj", "spec_id": "s1"})
    assert r.status_code == 422
