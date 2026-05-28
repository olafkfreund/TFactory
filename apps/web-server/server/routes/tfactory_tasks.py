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

import json as _json_mod

from fastapi import (
    APIRouter,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status as http_status,
)


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


# ─── Logs endpoint — testable core + WS transport ──────────────────────


# Cap how many lines per log file we send per payload. Keeps the WS
# message size predictable; the frontend can request more via a query
# param in future commits.
DEFAULT_LOG_TAIL_LINES = 200

# Cap how many bytes per file we ever read for tailing — prevents a
# 100MB log file from blowing the memory budget. We seek to the end
# and read at most this many bytes.
_TAIL_READ_CAP_BYTES = 1_000_000


def _resolve_log_files(spec_dir: Path) -> dict[str, Path]:
    """Discover log files under ``spec_dir/logs/``.

    Returns a mapping ``{stem: path}`` where stem is the filename
    without ``.log`` extension (e.g., ``planner``, ``gen_functional``).
    Empty dict if logs/ doesn't exist.
    """
    logs_dir = spec_dir / "logs"
    if not logs_dir.exists() or not logs_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for path in sorted(logs_dir.glob("*.log")):
        if path.is_file():
            out[path.stem] = path
    return out


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last ``n`` lines of ``path``.

    Caps the read at ``_TAIL_READ_CAP_BYTES`` from EOF — guards against
    huge log files. Lines are stripped of the trailing newline. Returns
    an empty list if the file can't be read.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_READ_CAP_BYTES:
                fh.seek(size - _TAIL_READ_CAP_BYTES)
                # Discard the (likely partial) leading line
                fh.readline()
            tail_bytes = fh.read()
    except OSError:
        return []
    text = tail_bytes.decode("utf-8", errors="replace")
    # Use splitlines so we don't emit a trailing empty string from a
    # final newline.
    lines = text.splitlines()
    return lines[-n:] if n > 0 else []


def tail_log_payload(spec_id: str, lines_per_file: int = DEFAULT_LOG_TAIL_LINES) -> dict:
    """Build the JSON payload the WS endpoint sends.

    Returns:
        {
          "spec_id": "<id>",
          "captured_at": "<iso>",
          "files": {
            "planner": ["line1", "line2", ...],
            "gen_functional": [...],
            ...
          }
        }

    Raises:
        HTTPException 400 — malformed spec_id
        HTTPException 404 — spec dir doesn't exist
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    files = _resolve_log_files(spec_dir)
    payload_files = {
        name: _tail_lines(path, lines_per_file)
        for name, path in files.items()
    }
    return {
        "spec_id": spec_id,
        "captured_at": datetime.utcnow().isoformat(timespec="seconds") + "+00:00",
        "files": payload_files,
    }


@router.websocket("/{spec_id}/logs/stream")
async def websocket_logs(websocket: WebSocket, spec_id: str) -> None:
    """WebSocket log tail. Sends one ``tail_log_payload`` snapshot on
    connect; the frontend polls via reconnect for refreshes (live tail
    is a Task 11 follow-up).

    Disconnects cleanly on close. Path-traversal / missing-spec are
    reported by closing with code 4400 / 4404 so the frontend can
    distinguish them.
    """
    await websocket.accept()
    try:
        try:
            payload = tail_log_payload(spec_id)
        except HTTPException as exc:
            # 400 → close with 4400, 404 → 4404 (custom WS close codes)
            code = 4400 if exc.status_code == 400 else 4404
            await websocket.close(code=code, reason=exc.detail)
            return
        await websocket.send_text(_json_mod.dumps(payload))
        # Stay open until the client disconnects. The MVP doesn't
        # push updates — Task 11's e2e smoke will revisit live tail.
        try:
            while True:
                # recv_text raises WebSocketDisconnect on close.
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
    except Exception:  # noqa: BLE001 — protect the ws loop from leaks
        try:
            await websocket.close(code=1011)  # internal error
        except Exception:  # noqa: BLE001
            pass


# ─── Catalog endpoint (Task 14 / #30) ──────────────────────────────────────


@router.get("/{spec_id}/catalog")
def get_catalog(spec_id: str) -> Response:
    """Serve the spec's ``context/tests_catalog.json`` snapshot.

    The snapshot is written by the AIFactory snapshotter (Task 4) when the
    handover happens.  It captures the state of the AIFactory repo's
    ``tests-catalog.json`` at the moment the spec was handed to TFactory.

    Returns the catalog JSON verbatim.

    Returns 400 on malformed spec_id, 404 if the spec is not found or the
    catalog has not been snapshotted for this spec yet.

    URL: ``GET /api/tfactory/tasks/{spec_id}/catalog``
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    catalog_path = spec_dir / "context" / "tests_catalog.json"
    if not catalog_path.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="catalog not snapshotted for this spec",
        )

    try:
        content = catalog_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not read catalog: {exc}",
        ) from exc

    return Response(content=content, media_type="application/json")
