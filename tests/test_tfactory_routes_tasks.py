"""Tests for /api/tfactory/tasks REST endpoints — Task 9 (#10) commit 1.

The route handlers are plain Python functions decorated with
``@router.get(...)`` — we test them directly (no HTTP stack
required). FastAPI's own behaviour (URL routing, response model
serialisation) is exercised by FastAPI's own test suite; here we
focus on OUR business logic.

Covered:
  - Workspace root resolution (env override + default)
  - spec_id validation (path traversal protection)
  - list_tasks: empty workspace, single task, multi-task ordering
  - get_task: 400 on malformed id, 404 on missing, full status_doc
    returned on hit, artefact meta with existence flags
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest


# The route module imports FastAPI's APIRouter + HTTPException. The
# TFactory backend venv does NOT install FastAPI (those deps live in
# apps/web-server/requirements.txt and are only installed when the
# portal runs). For unit-level coverage of the route's business logic
# we install a minimal stub into sys.modules BEFORE importing the
# route file — fast, no extra deps, exercises our code.
if "fastapi" not in sys.modules:
    _fastapi = types.ModuleType("fastapi")

    class _APIRouter:
        def __init__(self, *args, **kwargs): pass
        def get(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator

    class _HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _status = types.ModuleType("fastapi.status")
    _status.HTTP_400_BAD_REQUEST = 400
    _status.HTTP_404_NOT_FOUND = 404

    _fastapi.APIRouter = _APIRouter
    _fastapi.HTTPException = _HTTPException
    _fastapi.status = _status
    sys.modules["fastapi"] = _fastapi
    sys.modules["fastapi.status"] = _status

from fastapi import HTTPException as _HTTPException  # noqa: E402


# Add apps/web-server/ to sys.path so ``from server.routes...`` resolves.
WEB_SERVER_PATH = (
    Path(__file__).parent.parent / "apps" / "web-server"
)
if str(WEB_SERVER_PATH) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_PATH))


from server.routes.tfactory_tasks import (  # noqa: E402
    _artefact_meta,
    _find_spec_dir,
    _resolve_workspace_root,
    _summary_row,
    _validate_spec_id,
    get_task,
    list_tasks,
)


# ── Workspace builder fixture ──────────────────────────────────────────


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fresh ~/.tfactory-shaped tmp dir and point the env at it."""
    root = tmp_path / "tfactory"
    (root / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(root))
    return root


def _make_task(
    workspace_root: Path,
    *,
    project_id: str,
    spec_id: str,
    status: str = "evaluated",
    phase: str = "evaluator_complete",
    updated_at: str = "2026-05-28T10:00:00+00:00",
    extra_status: dict[str, Any] | None = None,
    artefacts: list[str] | None = None,
) -> Path:
    """Scaffold a TFactory spec_dir with a status.json + optional artefacts."""
    spec_dir = workspace_root / "workspaces" / project_id / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "findings").mkdir()
    doc = {
        "task_id": spec_id,
        "project_id": project_id,
        "spec_id": spec_id,
        "status": status,
        "phase": phase,
        "updated_at": updated_at,
    }
    if extra_status:
        doc.update(extra_status)
    (spec_dir / "status.json").write_text(json.dumps(doc))
    for relpath in artefacts or []:
        path = spec_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if relpath.endswith(".json") else "# x")
    return spec_dir


# ── Workspace root resolution ──────────────────────────────────────────


def test_resolve_workspace_root_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", "/var/lib/tf")
    assert _resolve_workspace_root() == Path("/var/lib/tf")


def test_resolve_workspace_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TFACTORY_WORKSPACE_ROOT", raising=False)
    root = _resolve_workspace_root()
    # Default is ~/.tfactory regardless of platform
    assert root.name == ".tfactory"
    assert root.parent == Path.home()


def test_resolve_workspace_root_expands_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", "~/.tf-custom")
    root = _resolve_workspace_root()
    # ~ should be expanded
    assert not str(root).startswith("~")


# ── spec_id validation ─────────────────────────────────────────────────


def test_validate_spec_id_accepts_simple() -> None:
    _validate_spec_id("042-session-expiry")  # no raise
    _validate_spec_id("simple")
    _validate_spec_id("a-b_c.d-1")


def test_validate_spec_id_rejects_path_traversal() -> None:
    for bad in ("../etc/passwd", "../../x", "x/y", "a/../b"):
        with pytest.raises(_HTTPException) as exc:
            _validate_spec_id(bad)
        assert exc.value.status_code == 400


def test_validate_spec_id_rejects_empty() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_spec_id("")
    assert exc.value.status_code == 400


def test_validate_spec_id_rejects_spaces() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_spec_id("foo bar")
    assert exc.value.status_code == 400


# ── list_tasks ──────────────────────────────────────────────────────────


def test_list_tasks_empty_workspace(workspace_root: Path) -> None:
    """Workspace dir exists but no projects — empty list."""
    result = list_tasks()
    assert result == {"tasks": [], "count": 0}


def test_list_tasks_no_root_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace root doesn't exist at all — graceful empty response."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "nonexistent"))
    result = list_tasks()
    assert result == {"tasks": [], "count": 0}


def test_list_tasks_single(workspace_root: Path) -> None:
    _make_task(
        workspace_root,
        project_id="demo",
        spec_id="042-x",
        status="evaluated",
        phase="evaluator_complete",
    )
    result = list_tasks()
    assert result["count"] == 1
    row = result["tasks"][0]
    assert row["task_id"] == "042-x"
    assert row["project_id"] == "demo"
    assert row["spec_id"] == "042-x"
    assert row["status"] == "evaluated"
    assert row["phase"] == "evaluator_complete"


def test_list_tasks_sorted_newest_first(workspace_root: Path) -> None:
    _make_task(
        workspace_root, project_id="demo", spec_id="older",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    _make_task(
        workspace_root, project_id="demo", spec_id="newer",
        updated_at="2026-05-28T00:00:00+00:00",
    )
    result = list_tasks()
    assert result["count"] == 2
    # Newest first
    assert result["tasks"][0]["spec_id"] == "newer"
    assert result["tasks"][1]["spec_id"] == "older"


def test_list_tasks_across_multiple_projects(workspace_root: Path) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="d1")
    _make_task(workspace_root, project_id="other", spec_id="o1")
    result = list_tasks()
    assert result["count"] == 2
    project_ids = {r["project_id"] for r in result["tasks"]}
    assert project_ids == {"demo", "other"}


def test_list_tasks_skips_malformed_status_json(workspace_root: Path) -> None:
    """A spec_dir with broken status.json still gets a row — fields
    fall back to defaults."""
    spec_dir = (
        workspace_root / "workspaces" / "demo" / "specs" / "broken"
    )
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text("not json{")

    result = list_tasks()
    assert result["count"] == 1
    row = result["tasks"][0]
    # task_id falls back to spec_id when status.json doesn't parse
    assert row["task_id"] == "broken"
    assert row["spec_id"] == "broken"
    assert row["status"] is None  # field absent in malformed doc


# ── get_task ───────────────────────────────────────────────────────────


def test_get_task_happy_returns_full_status(workspace_root: Path) -> None:
    _make_task(
        workspace_root, project_id="demo", spec_id="042-x",
        status="triaged",
        extra_status={
            "verdicts_count": 6,
            "committed_count": 4,
            "flagged_count": 1,
            "rejected_count": 1,
        },
    )
    result = get_task("042-x")
    assert result["task_id"] == "042-x"
    assert result["project_id"] == "demo"
    assert result["spec_id"] == "042-x"
    # Full status doc surfaced
    sj = result["status_json"]
    assert sj["status"] == "triaged"
    assert sj["verdicts_count"] == 6
    assert sj["committed_count"] == 4


def test_get_task_404_when_missing(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc:
        get_task("nonexistent")
    assert exc.value.status_code == 404


def test_get_task_400_on_malformed_id(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc:
        get_task("../../etc/passwd")
    assert exc.value.status_code == 400


def test_get_task_artefacts_meta_existence_flags(
    workspace_root: Path,
) -> None:
    _make_task(
        workspace_root, project_id="demo", spec_id="042-x",
        artefacts=[
            "test_plan.json",
            "findings/verdicts.json",
            "findings/triage_report.json",
            "findings/triage_report.md",
        ],
    )
    result = get_task("042-x")
    arts = result["artefacts"]
    assert arts["test_plan"]["exists"] is True
    assert arts["verdicts"]["exists"] is True
    assert arts["triage_report_json"]["exists"] is True
    assert arts["triage_report_md"]["exists"] is True
    # Unwritten artefact still listed with exists=False
    assert arts["pr_comment_body"]["exists"] is False


def test_get_task_artefacts_paths_relative(workspace_root: Path) -> None:
    """Artefact paths are spec_dir-relative — frontend joins them to
    its own base URL, doesn't need absolute filesystem paths."""
    _make_task(
        workspace_root, project_id="demo", spec_id="042-x",
        artefacts=["test_plan.json"],
    )
    result = get_task("042-x")
    assert result["artefacts"]["test_plan"]["path"] == "test_plan.json"
    assert result["artefacts"]["verdicts"]["path"] == "findings/verdicts.json"


def test_get_task_no_status_json_returns_empty_doc(
    workspace_root: Path,
) -> None:
    """A spec_dir with NO status.json → 404 (we use status.json's
    presence as the existence sentinel)."""
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "incomplete"
    spec_dir.mkdir(parents=True)
    # No status.json written

    with pytest.raises(_HTTPException) as exc:
        get_task("incomplete")
    assert exc.value.status_code == 404
