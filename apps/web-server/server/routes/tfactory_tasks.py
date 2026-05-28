"""TFactory task endpoints — Task 9 (#10) commit 1.

Read-only endpoints over the TFactory workspace filesystem
(``~/.tfactory/workspaces/{project_id}/specs/{spec_id}/``). Powers the
portal retheme (Task 10's frontend lane status grid).

Endpoints in this commit:
  - GET /api/tfactory/tasks                 — list workspaces
  - GET /api/tfactory/tasks/{spec_id}        — status detail

Future commits add:
  - report.md / report.json / verdicts.json artefact endpoints
  - WebSocket log stream

Workspace root resolution:
  - Env ``TFACTORY_WORKSPACE_ROOT`` if set (typically the value the
    backend agents already use), else ``~/.tfactory``.
  - Workspaces live under ``<root>/workspaces/<project_id>/specs/<spec_id>/``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status as http_status


router = APIRouter()


# ─── Path resolution ────────────────────────────────────────────────────


def _resolve_workspace_root() -> Path:
    """Resolve the TFactory workspace root.

    Defaults to ``~/.tfactory``. Override via env ``TFACTORY_WORKSPACE_ROOT``
    (tests use this to point at a tmp directory)."""
    env_val = os.environ.get("TFACTORY_WORKSPACE_ROOT")
    if env_val:
        return Path(env_val).expanduser()
    return Path.home() / ".tfactory"


_SPEC_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_spec_id(spec_id: str) -> None:
    """Reject path-traversal attempts in the spec_id path parameter."""
    if not spec_id or not _SPEC_ID_RE.match(spec_id):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"invalid spec_id: {spec_id!r}",
        )


def _find_spec_dir(root: Path, spec_id: str) -> Path | None:
    """Locate the spec_dir for ``spec_id`` across all projects under root.

    Returns the first match (spec_ids are unique within a project but
    technically could collide across projects; production caller picks
    the right project via list endpoint).
    """
    workspaces = root / "workspaces"
    if not workspaces.exists():
        return None
    for project_dir in workspaces.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / "specs" / spec_id
        if (candidate / "status.json").exists():
            return candidate
    return None


# ─── Helpers ────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict | None:
    """Best-effort JSON read. None on missing / parse failure."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _summary_row(status_path: Path) -> dict[str, Any]:
    """Pluck the public fields from a status.json for the list response.

    Adds derived ``project_id`` + ``spec_id`` (computed from the path)
    + ``updated_at`` (mtime fallback if status.json lacks the field)."""
    status_doc = _read_json(status_path) or {}
    spec_dir = status_path.parent
    project_id = spec_dir.parent.parent.name  # workspaces/<pid>/specs/<sid>/
    spec_id = spec_dir.name

    updated_at = status_doc.get("updated_at")
    if not updated_at:
        try:
            mtime = status_path.stat().st_mtime
            updated_at = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        except OSError:
            updated_at = ""

    return {
        "task_id": status_doc.get("task_id") or spec_id,
        "project_id": project_id,
        "spec_id": spec_id,
        "status": status_doc.get("status"),
        "phase": status_doc.get("phase"),
        "updated_at": updated_at,
    }


def _artefact_meta(spec_dir: Path) -> dict[str, dict]:
    """Build the meta block listing artefact paths + existence flags.

    The portal frontend reads this to decide which 'fetch report' buttons
    to enable.
    """
    artefacts = {
        "test_plan": spec_dir / "test_plan.json",
        "verdicts": spec_dir / "findings" / "verdicts.json",
        "triage_report_json": spec_dir / "findings" / "triage_report.json",
        "triage_report_md": spec_dir / "findings" / "triage_report.md",
        "pr_comment_body": spec_dir / "findings" / "pr_comment_body.md",
    }
    return {
        name: {
            "path": str(path.relative_to(spec_dir)),
            "exists": path.exists(),
        }
        for name, path in artefacts.items()
    }


# ─── Endpoints ──────────────────────────────────────────────────────────


@router.get("")
def list_tasks() -> dict:
    """List all TFactory tasks across all projects.

    Response shape:
        {
          "tasks": [
            {"task_id", "project_id", "spec_id", "status", "phase", "updated_at"},
            ...
          ],
          "count": N
        }

    Sorted by ``updated_at`` descending (most recent first). Empty list
    if the workspace root doesn't exist yet.
    """
    root = _resolve_workspace_root()
    workspaces = root / "workspaces"
    rows: list[dict] = []
    if workspaces.exists():
        for status_path in workspaces.glob("*/specs/*/status.json"):
            rows.append(_summary_row(status_path))
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return {"tasks": rows, "count": len(rows)}


@router.get("/{spec_id}")
def get_task(spec_id: str) -> dict:
    """Fetch full status.json for a task + artefact-existence meta.

    Returns 400 on malformed spec_id, 404 if no matching spec dir.
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    status_doc = _read_json(spec_dir / "status.json") or {}
    project_id = spec_dir.parent.parent.name
    return {
        "task_id": status_doc.get("task_id") or spec_id,
        "project_id": project_id,
        "spec_id": spec_id,
        "status_json": status_doc,
        "artefacts": _artefact_meta(spec_dir),
    }


# ─── Artefact endpoints ────────────────────────────────────────────────


def _serve_artefact_file(
    spec_id: str, relpath: str, media_type: str,
) -> Response:
    """Locate ``spec_id`` and serve the file at ``spec_dir/relpath``.

    Returns 400 on malformed spec_id, 404 if spec missing or artefact
    missing. Returns raw bytes verbatim — the frontend parses /
    renders as appropriate.
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )
    target = spec_dir / relpath
    if not target.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"artefact not found: {relpath}",
        )
    try:
        content = target.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not read artefact: {exc}",
        ) from exc
    return Response(content=content, media_type=media_type)


@router.get("/{spec_id}/verdicts.json")
def get_verdicts(spec_id: str) -> Response:
    """Stream the Evaluator's verdicts.json verbatim."""
    return _serve_artefact_file(
        spec_id, "findings/verdicts.json", "application/json",
    )


@router.get("/{spec_id}/triage-report.json")
def get_triage_report_json(spec_id: str) -> Response:
    """Stream the Triager's triage_report.json verbatim."""
    return _serve_artefact_file(
        spec_id, "findings/triage_report.json", "application/json",
    )


@router.get("/{spec_id}/triage-report.md")
def get_triage_report_md(spec_id: str) -> Response:
    """Stream the Triager's triage_report.md verbatim."""
    return _serve_artefact_file(
        spec_id, "findings/triage_report.md", "text/markdown",
    )


@router.get("/{spec_id}/test-plan.json")
def get_test_plan(spec_id: str) -> Response:
    """Stream the Planner's test_plan.json verbatim."""
    return _serve_artefact_file(
        spec_id, "test_plan.json", "application/json",
    )


@router.get("/{spec_id}/pr-comment-body.md")
def get_pr_comment_body(spec_id: str) -> Response:
    """Stream findings/pr_comment_body.md — present when the Triager
    skipped a real gh pr comment (no PR number in source.json)."""
    return _serve_artefact_file(
        spec_id, "findings/pr_comment_body.md", "text/markdown",
    )
