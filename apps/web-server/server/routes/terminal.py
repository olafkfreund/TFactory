"""
Terminal management routes.

REST endpoints for creating, listing, and destroying terminal sessions.
WebSocket I/O is handled in websockets/terminal.py.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from server.error_ref import InputRejectedError, client_error
from server.pty.access import (
    may_use,
    project_id_for_path,
)
from server.pty.manager import DuplicateSessionError, get_pty_manager
from server.pty.session import PTYSession
from server.routes.project_authz import (
    canonical_project_path,
    is_service_principal,
    require_project_role,
)

from ..config import get_settings
from ..services.terminal_worktree_service import TerminalWorktreeService
from ._specpath import safe_component
from .projects import load_projects

router = APIRouter()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class CreateTerminalRequest(BaseModel):
    """Request to create a new terminal."""

    id: str | None = Field(
        None,
        description="Terminal ID (optional - will be generated if not provided)",
    )
    project_id: str | None = Field(
        None,
        alias="projectId",
        description="Project ID - terminal will open in project directory",
    )
    cwd: str | None = Field(
        None,
        description="Working directory (overrides project_id)",
    )
    project_path: str | None = Field(
        None,
        alias="projectPath",
        description="Project path (alternative to project_id)",
    )
    shell: str | None = Field(
        None,
        description="Shell to use (defaults to /bin/bash)",
    )
    cols: int = Field(80, description="Terminal width in columns")
    rows: int = Field(24, description="Terminal height in rows")

    class Config:
        populate_by_name = True


class ResizeTerminalRequest(BaseModel):
    """Request to resize a terminal."""

    cols: int = Field(..., description="Terminal width in columns")
    rows: int = Field(..., description="Terminal height in rows")


class TerminalInfo(BaseModel):
    """Terminal session information with frontend-compatible field names.

    Uses camelCase field names with snake_case aliases for backward compatibility
    with data from PTY manager.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    cwd: str
    shell: str
    cols: int
    rows: int
    # Frontend expects camelCase, PTY manager returns snake_case
    createdAt: str = Field(alias="created_at")
    isAlive: bool = Field(alias="is_alive")


class TerminalListResponse(BaseModel):
    """Response for listing terminals."""

    terminals: list[TerminalInfo]
    count: int
    max_terminals: int


class CreateTerminalWorktreeRequest(BaseModel):
    """Request to create a new terminal worktree."""

    model_config = ConfigDict(populate_by_name=True)

    terminalId: str = Field(..., description="Terminal ID")
    name: str = Field(
        ..., description="Worktree name (lowercase, alphanumeric, dashes, underscores)"
    )
    taskId: str | None = Field(None, description="Optional task ID association")
    createGitBranch: bool = Field(True, description="Whether to create a git branch")
    projectPath: str = Field(..., description="Project path")
    baseBranch: str = Field("main", description="Base branch to branch from")


class TerminalWorktreeConfig(BaseModel):
    """Configuration for a terminal worktree."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    path: str
    branch: str | None = None
    baseBranch: str = Field(alias="base_branch")
    taskId: str | None = Field(None, alias="task_id")
    createdAt: str = Field(alias="created_at")
    terminalId: str = Field(alias="terminal_id")


class TerminalWorktreeResult(BaseModel):
    """Result of worktree operation."""

    success: bool
    config: TerminalWorktreeConfig | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# Ownership (#3740)
# --------------------------------------------------------------------------


def _user(http_request: Request) -> dict[str, Any] | None:
    return getattr(http_request.state, "user", None)


def _service_only(http_request: Request) -> None:
    if not is_service_principal(_user(http_request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation is limited to the service principal",
        )


async def _authorize_path(
    user: dict[str, Any] | None, path: str | None, role: str = "admin"
) -> str:
    """Authorize the project containing `path`."""
    # The role check below rejects unknown paths before deletion.
    resolved = (
        str(canonical_project_path(path, allow_unregistered=is_service_principal(user)))
        if path
        else ""
    )
    project_id = project_id_for_path(resolved)
    await require_project_role(user, project_id, role)
    return resolved


async def _owned(
    terminal_id: str, http_request: Request, role: str = "viewer"
) -> PTYSession:
    """The session if the caller may use it; another user's reads as missing."""
    session = get_pty_manager().get_session(terminal_id)
    if session is None or not await may_use(_user(http_request), session, role):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found"
        )
    return session


async def _visible(http_request: Request) -> list[PTYSession]:
    user = _user(http_request)
    manager = get_pty_manager()
    return [s for s in list(manager.sessions.values()) if await may_use(user, s)]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("", response_model=TerminalListResponse)
async def list_terminals(http_request: Request):
    """List the caller's active terminal sessions."""
    settings = get_settings()

    terminals = [TerminalInfo(**s.to_dict()) for s in await _visible(http_request)]

    return TerminalListResponse(
        terminals=terminals,
        count=len(terminals),
        max_terminals=settings.MAX_TERMINALS,
    )


@router.post("", response_model=TerminalInfo, status_code=status.HTTP_201_CREATED)
async def create_terminal(request: CreateTerminalRequest, http_request: Request):
    """Create a new terminal session.

    After creating, connect to /ws/terminal/{id} for I/O.
    """
    manager = get_pty_manager()
    settings = get_settings()

    # Determine working directory
    cwd = request.cwd

    # Try projectPath first (direct path from frontend)
    if not cwd and request.project_path:
        cwd = request.project_path

    # Then try project_id lookup
    if not cwd and request.project_id:
        projects = load_projects()
        if request.project_id in projects:
            cwd = projects[request.project_id]["path"]

    if not cwd:
        cwd = str(Path.home())

    # The UI re-posts create on every remount of a live terminal: hand the
    # caller's own live session back instead of replacing or rejecting it.
    user = _user(http_request)
    existing = manager.get_session(request.id) if request.id else None
    if existing is not None and await may_use(user, existing, "member"):
        if existing.is_alive():
            return TerminalInfo(**existing.to_dict())
        manager.close_session(existing.id, expected=existing)

    # Bind the session (#3740). Non-service callers must land inside a
    # registered project they are authorized for.
    # The resolved cwd is bound to a registered project and authorized before session creation.
    cwd = str(
        canonical_project_path(cwd, allow_unregistered=is_service_principal(user))
    )
    project_id = project_id_for_path(cwd)
    if not settings.DISABLE_AUTH and (
        not project_id or (request.project_id and request.project_id != project_id)
    ):
        raise HTTPException(status_code=404, detail="Project not found")
    if project_id:
        await require_project_role(user, project_id, "admin")
    if not Path(cwd).exists():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {cwd}")

    try:
        session = manager.create_session(
            cwd=cwd,
            shell=request.shell or settings.DEFAULT_SHELL,
            cols=request.cols,
            rows=request.rows,
            session_id=request.id,  # Use frontend-provided ID if available
            owner_id=user.get("id") if isinstance(user, dict) else None,
            project_id=project_id,
        )
    except DuplicateSessionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Terminal id already exists",
        ) from None
    except InputRejectedError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=client_error(logger, "could not create terminal session", e),
        ) from e

    return TerminalInfo(**session.to_dict())


# --------------------------------------------------------------------------
# Specific Routes (must come BEFORE /{terminal_id} catch-all)
# --------------------------------------------------------------------------


@router.get("/session-dates")
async def get_session_dates(project: str | None = None):
    """Get available session dates for restoration."""
    return {"success": True, "data": []}


@router.get("/sessions")
async def get_sessions(http_request: Request, project: str | None = None):
    """Get the caller's terminal sessions, for a project when given."""
    sessions = await _visible(http_request)
    if project:
        project_id = project_id_for_path(project)
        sessions = [s for s in sessions if project_id and s.project_id == project_id]
    return {"success": True, "data": [s.to_dict() for s in sessions]}


@router.post("/cleanup")
async def cleanup_terminals(http_request: Request):
    """Clean up dead terminal sessions."""
    _service_only(http_request)
    manager = get_pty_manager()
    cleaned = manager.cleanup_dead_sessions()
    return {"cleaned": cleaned}


@router.post("/restore")
async def restore_session(session: dict):
    """Restore a terminal session."""
    return {"success": False, "error": "Session restoration not yet implemented"}


@router.post("/restore-date")
async def restore_sessions_from_date(request: dict):
    """Restore terminal sessions from a date."""
    return {"success": False, "error": "Session restoration not yet implemented"}


@router.delete("/sessions")
async def clear_terminal_sessions(http_request: Request, project: str | None = None):
    """Clear terminal session history.

    Removes saved session files from the terminal-sessions directory.
    If project is specified, only clears sessions for that project.
    Otherwise clears all sessions from the default location.

    Args:
        project: Optional project path to limit session cleanup

    Returns:
        dict: Success response with count of cleared sessions
    """

    if project:
        project = await _authorize_path(_user(http_request), project)
    else:
        _service_only(http_request)

    cleared_count = 0
    errors: list[str] = []
    failed_files = 0
    failed_dirs = 0

    # Determine which directories to clear
    dirs_to_clear = []

    if project:
        # Clear sessions for a specific project. Resolve the request-supplied
        # path against the registered projects and use the stored (trusted)
        # path, so deletion can only target a known project's session
        # directory, never an arbitrary filesystem location (py/path-injection).
        projects = load_projects()
        trusted_path = next(
            (
                p["path"]
                for p in projects.values()
                if isinstance(p, dict) and p.get("path") == project
            ),
            None,
        )
        if trusted_path:
            project_path = Path(trusted_path)
            if project_path.exists():
                sessions_dir = project_path / ".tfactory" / "terminal-sessions"
                if sessions_dir.exists():
                    dirs_to_clear.append(sessions_dir)
    else:
        # Clear default sessions location
        default_sessions_dir = Path.home() / ".tfactory" / "terminal-sessions"
        if default_sessions_dir.exists():
            dirs_to_clear.append(default_sessions_dir)

        # Also try to find project sessions
        from ..paths import get_data_file

        projects_file = get_data_file("projects.json")
        if projects_file.exists():
            try:
                projects_data = json.loads(projects_file.read_text())
                # Handle both dict and list formats
                if isinstance(projects_data, dict):
                    projects_list = list(projects_data.values())
                else:
                    projects_list = projects_data

                for proj in projects_list:
                    proj_path = Path(
                        proj.get("path", "") if isinstance(proj, dict) else proj
                    )
                    sessions_dir = proj_path / ".tfactory" / "terminal-sessions"
                    if sessions_dir.exists():
                        dirs_to_clear.append(sessions_dir)
            except (json.JSONDecodeError, KeyError):
                pass

    # Clear session files from each directory.
    #
    # The warnings below reach the HTTP response, so they must NOT carry `str(e)`
    # or an absolute server path (py/stack-trace-exposure): an OSError stringifies
    # to its own path, and `sessions_dir` is an absolute on-disk location — both
    # hand an external caller our filesystem layout. Same treatment the rest of
    # this module already applies (#569/#570): the detail goes to the log, the
    # caller gets a count.
    for sessions_dir in dirs_to_clear:
        try:
            # Count and remove session files
            for session_file in sessions_dir.glob("terminal_*.json"):
                try:
                    session_file.unlink()
                    cleared_count += 1
                except Exception:
                    logger.exception(
                        "Failed to remove terminal session file %s", session_file
                    )
                    failed_files += 1
        except Exception:
            logger.exception("Failed to process terminal sessions dir %s", sessions_dir)
            failed_dirs += 1

    if failed_files:
        errors.append(f"{failed_files} session file(s) could not be removed")
    if failed_dirs:
        errors.append(f"{failed_dirs} session director(ies) could not be processed")

    result = {
        "success": True,
        "data": {
            "cleared": cleared_count,
            "message": f"Cleared {cleared_count} terminal session(s)",
        },
    }

    if errors:
        result["warnings"] = errors

    return result


@router.post("/generate-name")
async def generate_terminal_name(request: dict):
    """Generate a name for a terminal based on command."""
    command = request.get("command", "")
    cwd = request.get("cwd", "")
    if command:
        name = command.split()[0] if command else "Terminal"
    else:
        name = Path(cwd).name if cwd else "Terminal"
    return {"success": True, "data": name}


@router.get("/worktrees")
async def list_terminal_worktrees(http_request: Request, project: str = Query(...)):
    """List all terminal worktrees for a project.

    Args:
        project: Project path (query parameter)

    Returns:
        IPCResult with list of TerminalWorktreeConfig dicts
    """
    project = await _authorize_path(_user(http_request), project, "viewer")
    try:
        service = TerminalWorktreeService(project)
        worktrees = service.list_worktrees()
        return {"success": True, "data": worktrees}
    except Exception:
        logger.exception("Failed to list terminal worktrees")
        return {"success": False, "error": "Failed to list terminal worktrees"}


@router.post("/worktrees", response_model=TerminalWorktreeResult)
async def create_terminal_worktree(
    request: CreateTerminalWorktreeRequest, http_request: Request
):
    """Create a new terminal worktree.

    Args:
        request: CreateTerminalWorktreeRequest with worktree details

    Returns:
        TerminalWorktreeResult with success status and config/error
    """
    project_path = await _authorize_path(_user(http_request), request.projectPath)
    try:
        service = TerminalWorktreeService(project_path)
        config = service.create_worktree(
            name=request.name,
            terminal_id=request.terminalId,
            task_id=request.taskId,
            create_git_branch=request.createGitBranch,
            base_branch=request.baseBranch,
        )
        return TerminalWorktreeResult(
            success=True, config=TerminalWorktreeConfig(**config)
        )
    except ValueError as e:
        # Validation errors (invalid name, already exists, etc.)
        return TerminalWorktreeResult(success=False, error=str(e))
    except subprocess.CalledProcessError as e:
        # Git command errors
        error_msg = f"Git error: {e.stderr if e.stderr else str(e)}"
        return TerminalWorktreeResult(success=False, error=error_msg)
    except Exception as e:
        return TerminalWorktreeResult(success=False, error=str(e))


@router.delete("/worktrees/{name}")
async def remove_terminal_worktree(
    name: str,
    http_request: Request,
    project: str = Query(...),
    deleteBranch: bool = Query(False),
):
    """Remove a terminal worktree.

    Args:
        name: Worktree name (path parameter)
        project: Project path (query parameter)
        deleteBranch: Whether to also delete the git branch (query parameter)

    Returns:
        IPCResult with success status
    """
    project = await _authorize_path(_user(http_request), project)
    try:
        service = TerminalWorktreeService(project)
        success = service.remove_worktree(name, deleteBranch)
        return {"success": success}
    except ValueError:
        logger.exception("Invalid request to remove terminal worktree")
        return {"success": False, "error": "Failed to remove terminal worktree"}
    except subprocess.CalledProcessError:
        logger.exception("Git error while removing terminal worktree")
        return {"success": False, "error": "Git error while removing terminal worktree"}
    except Exception:
        logger.exception("Failed to remove terminal worktree")
        return {"success": False, "error": "Failed to remove terminal worktree"}


@router.get("/sessions/{date}")
async def get_sessions_for_date(date: str, project: str | None = None):
    """Get terminal sessions for a specific date."""
    return {"success": True, "data": []}


# --------------------------------------------------------------------------
# Dynamic Routes (/{terminal_id} - must come AFTER specific routes)
# --------------------------------------------------------------------------


@router.get("/{terminal_id}", response_model=TerminalInfo)
async def get_terminal(terminal_id: str, http_request: Request):
    """Get information about a specific terminal."""
    session = await _owned(terminal_id, http_request)
    return TerminalInfo(**session.to_dict())


@router.delete("/{terminal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_terminal(terminal_id: str, http_request: Request):
    """Close a terminal session."""
    session = await _owned(terminal_id, http_request, "member")

    if not get_pty_manager().close_session(terminal_id, expected=session):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Terminal not found",
        )


@router.post("/{terminal_id}/resize")
async def resize_terminal(
    terminal_id: str, request: ResizeTerminalRequest, http_request: Request
):
    """Resize a terminal.

    Note: This can also be done via WebSocket with a resize message.
    """
    session = await _owned(terminal_id, http_request, "member")

    session.resize(request.cols, request.rows)
    return {"success": True, "cols": request.cols, "rows": request.rows}


@router.post("/{terminal_id}/buffer")
async def save_terminal_buffer(terminal_id: str, request: dict, http_request: Request):
    """Save terminal buffer to session file.

    Persists terminal output to a session file in the project's .tfactory directory.
    The buffer content is saved with a timestamp for future restoration.

    Args:
        terminal_id: Terminal session ID
        request: Dictionary containing:
            - buffer (str): Terminal output content to save
            - projectId (str, optional): Project ID for determining save location
            - metadata (dict, optional): Additional metadata to save with buffer

    Returns:
        dict: Success response with session file path and size

    Raises:
        HTTPException: 404 if terminal not found, 400 for validation errors
    """
    # Validate terminal exists and belongs to the caller
    session = await _owned(terminal_id, http_request, "member")

    try:
        # Validate buffer content
        buffer_content = request.get("buffer", "")
        if not isinstance(buffer_content, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Buffer content must be a string",
            )

        # Get project ID from request or use default
        project_id = request.get("projectId")
        if not is_service_principal(_user(http_request)):
            project_id = session.project_id  # never a body-chosen destination
        metadata = request.get("metadata", {})

        # Determine save directory
        if project_id:
            # Try to get project path from projects.json
            projects = load_projects()
            if project_id in projects:
                project_path = Path(projects[project_id]["path"])
                sessions_dir = project_path / ".tfactory" / "terminal-sessions"
            else:
                # Project not found, use default location
                sessions_dir = Path.home() / ".tfactory" / "terminal-sessions"
        else:
            # No project ID, use default location
            sessions_dir = Path.home() / ".tfactory" / "terminal-sessions"

        # Create sessions directory if it doesn't exist
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Generate session filename with timestamp. terminal_id is request
        # supplied, so reduce it to a single literal path component before it
        # forms the session filename (py/path-injection).
        safe_terminal_id = safe_component(terminal_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_filename = f"terminal_{safe_terminal_id}_{timestamp}.json"
        session_file = sessions_dir / session_filename

        # Prepare session data
        session_data = {
            "terminalId": terminal_id,
            "timestamp": datetime.now().isoformat(),
            "cwd": session.cwd,
            "shell": session.shell,
            "buffer": buffer_content,
            "metadata": metadata,
            "createdAt": session.created_at.isoformat(),
        }

        # Save session to file
        session_file.write_text(json.dumps(session_data, indent=2))

        # Set secure file permissions (owner read/write only)
        session_file.chmod(0o600)

        # Get file size for response
        file_size = session_file.stat().st_size

        return {
            "success": True,
            "message": "Terminal buffer saved successfully",
            "sessionFile": str(session_file),
            "size": file_size,
            "timestamp": session_data["timestamp"],
        }

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=client_error(logger, "Failed to save terminal buffer", e),
        )


@router.get("/{terminal_id}/alive")
async def check_terminal_alive(terminal_id: str, http_request: Request):
    """Check if terminal PTY is alive."""
    session = get_pty_manager().get_session(terminal_id)
    if session is None or not await may_use(_user(http_request), session):
        return {"success": True, "data": False}
    return {"success": True, "data": session.is_alive()}


@router.post("/{terminal_id}/invoke-claude")
async def invoke_claude_in_terminal(terminal_id: str, request: dict = None):
    """Invoke Claude CLI in terminal."""
    return {"success": False, "error": "Claude invocation not yet implemented"}


@router.post("/{terminal_id}/resume-claude")
async def resume_claude_in_terminal(terminal_id: str, request: dict = None):
    """Resume Claude session in terminal."""
    return {"success": False, "error": "Claude resume not yet implemented"}
