"""POST /api/specs/ingest must self-heal a recycled on-disk clone (#539).

A registered project's working tree can vanish out from under TFactory — a pod
restart on an ephemeral volume, a PVC reset, a manual cleanup — while the
project DB record persists. The planner then resolves ``project_dir`` to that
now-missing path and the agent SDK raises "Working directory does not exist",
surfacing as ``status=planner_failed`` / ``phase=planner_initial_exception``
before any test lane runs.

``_ensure_project_clone`` re-materializes the clone from the stored
``clonedFrom`` origin (idempotent — same git URL → same workspace slug → same
path) rather than letting the planner crash, and fails with a clear 409 when
there's no origin to restore from.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

pytest.importorskip("fastapi")
from fastapi import HTTPException  # noqa: E402
from server.routes import projects as projects_store  # noqa: E402
from server.routes import specs  # noqa: E402
from server.services import project_workspace_service as pws  # noqa: E402


def test_existing_project_dir_guards_unsafe_paths(tmp_path):
    # Absolute + existing → returned; everything unsafe/missing → None, so the
    # tainted value never reaches the Path.is_dir sink as a live path.
    ws = tmp_path / "ok"
    ws.mkdir()
    assert specs._existing_project_dir(str(ws)) == str(ws)
    assert specs._existing_project_dir(None) is None
    assert specs._existing_project_dir("") is None
    assert specs._existing_project_dir("relative/dir") is None  # not absolute
    assert specs._existing_project_dir(f"{tmp_path}/../etc") is None  # traversal
    assert specs._existing_project_dir(str(tmp_path / "missing")) is None  # gone


@pytest.mark.asyncio
async def test_existing_clone_is_used_as_is(tmp_path, monkeypatch):
    # The on-disk clone is present → return it untouched, never re-clone.
    ws = tmp_path / "clone"
    ws.mkdir()
    calls: list[dict] = []

    async def fake_clone(**kw):
        calls.append(kw)
        return ws

    monkeypatch.setattr(pws, "clone_or_update", fake_clone)
    entry = {"path": str(ws), "clonedFrom": "https://example.test/y.git"}

    out = await specs._ensure_project_clone(entry, "pid", source_branch=None)

    assert out == str(ws)
    assert calls == []  # no re-clone when the working dir already exists


@pytest.mark.asyncio
async def test_missing_clone_is_rematerialized_and_persisted(tmp_path, monkeypatch):
    missing = tmp_path / "gone"  # deliberately never created
    recloned = tmp_path / "reclone"
    recloned.mkdir()
    seen: dict = {}

    async def fake_clone(*, git_url, branch, credential):
        seen.update(git_url=git_url, branch=branch)
        return recloned

    saved: dict = {}
    monkeypatch.setattr(pws, "clone_or_update", fake_clone)
    monkeypatch.setattr(
        projects_store, "load_projects", lambda: {"pid": {"path": str(missing)}}
    )
    monkeypatch.setattr(projects_store, "save_projects", lambda p: saved.update(p))
    entry = {
        "path": str(missing),
        "clonedFrom": "https://example.test/y.git",
        "clonedBranch": "main",
    }

    out = await specs._ensure_project_clone(entry, "pid", source_branch="ignored")

    resolved = str(recloned.resolve())
    assert out == resolved
    # clonedBranch wins over source_branch; the stored origin is cloned.
    assert seen == {"git_url": "https://example.test/y.git", "branch": "main"}
    # the re-materialized path is persisted to the store and back onto the entry.
    assert saved["pid"]["path"] == resolved
    assert entry["path"] == resolved


@pytest.mark.asyncio
async def test_missing_clone_without_origin_raises_409(tmp_path, monkeypatch):
    missing = tmp_path / "gone"

    async def fake_clone(**kw):  # pragma: no cover — must not be called
        raise AssertionError("must not attempt to clone without an origin")

    monkeypatch.setattr(pws, "clone_or_update", fake_clone)
    entry = {"path": str(missing)}  # registered, but no clonedFrom

    with pytest.raises(HTTPException) as ei:
        await specs._ensure_project_clone(entry, "pid", source_branch=None)

    assert ei.value.status_code == 409
    assert "working directory is missing" in ei.value.detail


@pytest.mark.asyncio
async def test_source_branch_used_when_no_cloned_branch(tmp_path, monkeypatch):
    missing = tmp_path / "gone"
    recloned = tmp_path / "rc"
    recloned.mkdir()
    seen: dict = {}

    async def fake_clone(*, git_url, branch, credential):
        seen.update(branch=branch)
        return recloned

    monkeypatch.setattr(pws, "clone_or_update", fake_clone)
    monkeypatch.setattr(projects_store, "load_projects", lambda: {})
    monkeypatch.setattr(projects_store, "save_projects", lambda p: None)
    entry = {"path": str(missing), "clonedFrom": "https://example.test/y.git"}

    await specs._ensure_project_clone(entry, "pid", source_branch="feature/z")

    assert seen["branch"] == "feature/z"  # falls back to source_branch
