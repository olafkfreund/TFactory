"""Tests for routing the projects route's persistence through ProjectStore (WS3 1c).

Asserts the live ``load_projects``/``save_projects`` helpers are behaviour-
identical in the default ``json`` backend (round-trip + on-disk file), and that
the ``db`` backend fails loudly from the sync path (the async per-endpoint
cutover is pending) rather than silently bypassing org-scoping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
for _p in (_WEB_SERVER, _BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from server.routes import projects as projects_mod  # noqa: E402
from server.services import project_store as ps  # noqa: E402
from server.services.project_store import JsonProjectStore  # noqa: E402


@pytest.fixture
def json_backend(tmp_path, monkeypatch):
    settings = SimpleNamespace(PROJECTS_DATA_DIR=str(tmp_path), PROJECTS_BACKEND="json")
    monkeypatch.setattr(projects_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(ps, "get_settings", lambda: settings)
    return tmp_path


def test_load_save_roundtrip_json(json_backend):
    assert projects_mod.load_projects() == {}  # no file yet
    data = {"p1": {"name": "Alpha", "path": "/a"}}
    projects_mod.save_projects(data)
    assert projects_mod.load_projects() == data
    # and it landed in the expected file
    on_disk = json.loads((json_backend / "projects.json").read_text())
    assert on_disk == data


def test_db_backend_guarded_from_sync_path(tmp_path, monkeypatch):
    settings = SimpleNamespace(PROJECTS_DATA_DIR=str(tmp_path), PROJECTS_BACKEND="db")
    monkeypatch.setattr(projects_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(ps, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="async project route path"):
        projects_mod.load_projects()


def test_json_store_sync_methods(tmp_path):
    store = JsonProjectStore(tmp_path / "p.json")
    assert store.load_all_sync() == {}
    store.save_all_sync({"x": {"name": "X", "path": "/x"}})
    assert store.load_all_sync() == {"x": {"name": "X", "path": "/x"}}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
