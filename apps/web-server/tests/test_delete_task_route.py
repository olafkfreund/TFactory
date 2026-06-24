"""Tests for DELETE /api/tasks/{task_id} — task removal.

Regression: a SPEC-INGEST task is keyed by a bare spec_id (no ``project_id:``
prefix), which is exactly what the cockpit's Remove action sends. The old
handler required a colon and 400'd, so a failed/stuck ingested task was
unremovable and kept reappearing via CFactory's reconcile poll. The handler now
resolves a bare spec_id by searching every project's workspace.
"""

import asyncio
from unittest.mock import patch

import pytest


def _make_spec(tmp_path, project_id, spec_id):
    project_path = tmp_path / project_id
    spec_dir = project_path / ".tfactory" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text("{}")
    return project_path, spec_dir


def test_delete_bare_spec_id_ingest_task(tmp_path):
    from server.routes import tasks

    project_path, spec_dir = _make_spec(tmp_path, "76ddfb71", "bench-go-hello-1")
    with patch.object(
        tasks, "load_projects", return_value={"76ddfb71": {"path": str(project_path)}}
    ):
        # No colon — the spec-ingest / cockpit-Remove shape. Must NOT 400.
        asyncio.run(tasks.delete_task("bench-go-hello-1"))
    assert not spec_dir.exists()


def test_delete_project_qualified_task(tmp_path):
    from server.routes import tasks

    project_path, spec_dir = _make_spec(tmp_path, "proj-1", "042-feat")
    with patch.object(
        tasks, "load_projects", return_value={"proj-1": {"path": str(project_path)}}
    ):
        asyncio.run(tasks.delete_task("proj-1:042-feat"))
    assert not spec_dir.exists()


def test_delete_unknown_spec_404(tmp_path):
    from fastapi import HTTPException
    from server.routes import tasks

    project_path = tmp_path / "proj-1"
    (project_path / ".tfactory" / "specs").mkdir(parents=True)
    with patch.object(
        tasks, "load_projects", return_value={"proj-1": {"path": str(project_path)}}
    ):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(tasks.delete_task("does-not-exist"))
    assert ei.value.status_code == 404


def test_delete_unknown_project_for_qualified_id_404(tmp_path):
    from fastapi import HTTPException
    from server.routes import tasks

    with patch.object(tasks, "load_projects", return_value={}):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(tasks.delete_task("ghost:spec"))
    assert ei.value.status_code == 404
