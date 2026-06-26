"""GitHub CLI / auth / token endpoints — extracted from routes/github.py (#360).

A focused sub-router for GitHub CLI checks, OAuth/device-flow auth, and token
handling, carved out of the 2k-LOC routes/github.py. Behaviour and paths
unchanged; main.py mounts it under the same /api/github prefix. Shared
helpers/models still live in routes/github.py and are imported here.

    GET  /api/github/cli/check | auth/check | auto-detect | auth/status | token
    POST /api/github/cli/install | auth/start | persist-token
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess

from fastapi import APIRouter, Query

from .github import (
    PersistTokenRequest,
    _persist_cli_token_to_project,
    run_gh_command,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/cli/check")
async def check_github_cli():
    """Check if GitHub CLI is installed."""
    result = run_gh_command(["--version"])
    version = None
    if result["success"]:
        # Parse version from "gh version 2.x.x (2024-...)"
        output = result.get("output", "")
        import re as _re

        m = _re.search(r"(\d+\.\d+\.\d+)", output)
        if m:
            version = m.group(1)
    return {
        "success": True,
        "data": {"installed": result["success"], "version": version},
    }


@router.post("/cli/install")
def install_github_cli():
    """Install GitHub CLI (gh) from the official GitHub repository.

    Uses the official install script from https://cli.github.com/ which:
    1. Adds the GitHub CLI apt repository
    2. Installs the gh package

    Requires root access (runs in Docker container as root entrypoint).
    Uses sync def to avoid blocking the event loop with subprocess.run.
    """
    import logging
    import shlex

    log = logging.getLogger(__name__)

    def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a command inside a login shell."""
        safe_cmd = " ".join(shlex.quote(a) for a in args)
        return subprocess.run(
            ["bash", "-l", "-c", safe_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # Step 1: Check if gh is already installed
    try:
        result = _run(["gh", "--version"], timeout=10)
        if result.returncode == 0:
            return {
                "success": True,
                "data": {
                    "message": "GitHub CLI is already installed",
                    "version": result.stdout.strip(),
                    "steps_completed": ["already-installed"],
                },
            }
    except Exception:
        pass

    # Step 2: Install gh using the official apt repository method
    # This works in the Docker container (Ubuntu-based)
    steps_completed: list[str] = []

    try:
        log.info("Installing GitHub CLI via official apt repository...")

        # Add GitHub CLI apt repo and install
        install_script = (
            "apt-get update -qq"
            " && apt-get install -y -qq --no-install-recommends"
            " gpg wget"
            " && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg"
            " | tee /usr/share/keyrings/githubcli-archive-keyring.gpg > /dev/null"
            " && echo 'deb [arch=$(dpkg --print-architecture)"
            " signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg]"
            " https://cli.github.com/packages stable main'"
            " | tee /etc/apt/sources.list.d/github-cli.list > /dev/null"
            " && apt-get update -qq"
            " && apt-get install -y -qq --no-install-recommends gh"
            " && rm -rf /var/lib/apt/lists/*"
        )

        result = subprocess.run(
            ["bash", "-c", install_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to install GitHub CLI: {result.stderr.strip()[-500:]}",
            }
        steps_completed.append("gh-installed")
        log.info("GitHub CLI installed successfully")
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Installation timed out (120s)"}
    except Exception:
        logger.exception("GitHub CLI installation failed")
        return {"success": False, "error": "Installation failed"}

    # Step 3: Verify installation
    version_str = "unknown"
    try:
        result = _run(["gh", "--version"], timeout=10)
        if result.returncode == 0:
            version_str = result.stdout.strip()
        else:
            return {
                "success": False,
                "error": f"Installation completed but verification failed: {result.stderr.strip()}",
            }
    except Exception:
        logger.exception("GitHub CLI installation verification failed")
        return {
            "success": False,
            "error": "Installation completed but verification failed",
        }

    return {
        "success": True,
        "data": {
            "message": "GitHub CLI installed successfully",
            "version": version_str,
            "steps_completed": steps_completed,
        },
    }


@router.get("/auth/check")
async def check_github_auth():
    """Check if user is authenticated with GitHub CLI."""
    result = run_gh_command(["auth", "status"])
    output = result.get("output", "")
    authenticated = result["success"] and "Logged in" in output
    username = ""
    if authenticated:
        user_result = run_gh_command(["api", "user", "-q", ".login"])
        if user_result["success"]:
            username = user_result["output"]
    return {
        "success": True,
        "data": {"authenticated": authenticated, "username": username},
    }


@router.get("/auto-detect")
async def auto_detect_github(projectId: str | None = Query(None)):
    """Auto-detect GitHub CLI authentication and username in one call.

    If projectId is provided, the CLI token is persisted directly to the
    project's .tfactory/.env file (server-side). The raw token is never
    included in the response.
    """
    # Check gh CLI is installed
    if not shutil.which("gh"):
        return {
            "success": True,
            "data": {"authenticated": False, "reason": "gh_not_installed"},
        }

    # Check auth status
    auth_result = run_gh_command(["auth", "status"])
    output = auth_result.get("output", "")
    if not (auth_result["success"] and "Logged in" in output):
        return {
            "success": True,
            "data": {"authenticated": False, "reason": "not_logged_in"},
        }

    # Get username
    username = ""
    user_result = run_gh_command(["api", "user", "-q", ".login"])
    if user_result["success"]:
        username = user_result["output"]

    # Persist token server-side if projectId provided
    token_persisted = False
    if projectId:
        token_persisted = _persist_cli_token_to_project(projectId)

    return {
        "success": True,
        "data": {
            "authenticated": True,
            "username": username,
            "tokenPersisted": token_persisted,
        },
    }


# Background state for GitHub auth flow
_gh_auth_proc: asyncio.subprocess.Process | None = None
_gh_auth_status: dict | None = None


async def _monitor_gh_auth(proc: asyncio.subprocess.Process):
    """Monitor gh auth login process in background and broadcast result."""
    global _gh_auth_status, _gh_auth_proc
    import logging

    log = logging.getLogger(__name__)

    try:
        await asyncio.wait_for(proc.wait(), timeout=300)

        if proc.returncode == 0:
            _gh_auth_status = {"complete": True, "success": True}
            log.info("[GitHub Auth] Authentication completed successfully")
        else:
            _gh_auth_status = {
                "complete": True,
                "success": False,
                "error": "Authentication flow did not complete. Please try again.",
            }
            log.warning(f"[GitHub Auth] Process exited with code {proc.returncode}")
    except asyncio.TimeoutError:
        _gh_auth_status = {
            "complete": True,
            "success": False,
            "error": "Authentication timed out after 5 minutes.",
        }
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as e:
        _gh_auth_status = {
            "complete": True,
            "success": False,
            "error": f"Authentication failed: {e}",
        }
    finally:
        _gh_auth_proc = None

    # Broadcast completion via WebSocket
    try:
        from ..websockets.events import broadcast_event

        await broadcast_event("github:auth-complete", _gh_auth_status)
    except Exception:
        pass


@router.post("/auth/start")
async def start_github_auth():
    """Start GitHub CLI authentication flow using device code.

    Returns the device code and URL immediately so the user can complete
    auth on any device. The gh process continues running in the background.
    Poll GET /auth/status or listen for the github:auth-complete WebSocket event.
    """
    global _gh_auth_proc, _gh_auth_status
    gh_path = shutil.which("gh")
    if not gh_path:
        return {
            "success": True,
            "data": {"success": False, "message": "GitHub CLI (gh) is not installed."},
        }

    # Kill any existing auth process
    if _gh_auth_proc is not None:
        try:
            _gh_auth_proc.kill()
        except Exception:
            pass
        _gh_auth_proc = None

    _gh_auth_status = None

    try:
        proc = await asyncio.create_subprocess_exec(
            gh_path,
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--git-protocol",
            "https",
            "--web",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
        _gh_auth_proc = proc

        device_code = None
        auth_url = None

        # Read stderr line by line until we get the device code + URL
        # gh outputs to stderr: "First, copy your one-time code: XXXX-XXXX"
        # then "... open ... https://github.com/login/device"
        async def extract_device_code():
            nonlocal device_code, auth_url
            for stream in [proc.stderr, proc.stdout]:
                if stream is None:
                    continue
                while True:
                    try:
                        line = await asyncio.wait_for(stream.readline(), timeout=15)
                    except asyncio.TimeoutError:
                        break
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    code_match = re.search(
                        r"one-time code:\s*([A-Z0-9]+-[A-Z0-9]+)", text
                    )
                    if code_match:
                        device_code = code_match.group(1)
                    url_match = re.search(
                        r"(https://github\.com/login/device\S*)", text
                    )
                    if url_match:
                        auth_url = url_match.group(1)
                    if device_code and auth_url:
                        return

        await extract_device_code()

        if not device_code:
            # Process may have exited already (e.g., already authenticated)
            if proc.returncode is not None and proc.returncode == 0:
                _gh_auth_status = {"complete": True, "success": True}
                return {
                    "success": True,
                    "data": {
                        "success": True,
                        "message": "Already authenticated with GitHub.",
                    },
                }
            return {
                "success": True,
                "data": {
                    "success": False,
                    "message": "Could not extract device code from gh CLI output.",
                },
            }

        # Start background monitor for process completion
        asyncio.create_task(_monitor_gh_auth(proc))

        return {
            "success": True,
            "data": {
                "success": True,
                "deviceCode": device_code,
                "authUrl": auth_url or "https://github.com/login/device",
                "awaiting": True,
            },
        }

    except Exception:
        logger.exception("Failed to start GitHub authentication")
        _gh_auth_proc = None
        return {
            "success": True,
            "data": {
                "success": False,
                "message": "Failed to start authentication",
            },
        }


@router.get("/auth/status")
async def check_github_auth_status():
    """Poll for GitHub auth flow completion.

    Returns the current status of the background gh auth login process.
    """
    global _gh_auth_status

    if _gh_auth_status and _gh_auth_status.get("complete"):
        result = {**_gh_auth_status}
        _gh_auth_status = None  # Clear after reading
        return {"success": True, "data": result}

    return {"success": True, "data": {"complete": False}}


@router.get("/token")
async def get_github_token():
    """Check if a GitHub auth token is available from CLI.

    Returns only a boolean flag -- the raw token is never exposed.
    """
    result = run_gh_command(["auth", "token"])
    has_token = result["success"] and bool(result.get("output"))
    return {"success": True, "data": {"hasToken": has_token}}


@router.post("/persist-token")
async def persist_github_token(request: PersistTokenRequest):
    """Persist the gh CLI token to a project's .tfactory/.env file.

    The raw token never appears in the response.
    """
    persisted = _persist_cli_token_to_project(request.projectId)
    if persisted:
        return {"success": True, "data": {"tokenPersisted": True}}
    return {"success": False, "error": "Failed to persist token"}
