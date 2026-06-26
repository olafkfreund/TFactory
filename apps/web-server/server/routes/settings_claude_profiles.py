"""Claude-profile settings endpoints — extracted from routes/settings.py (#360).

A focused sub-router for Claude OAuth-profile management, carved out of the
2.4k-LOC routes/settings.py. Behaviour and paths unchanged; main.py mounts it
under the same /api/settings prefix. Shared helpers/models still live in
routes/settings.py and are imported here.

    /api/settings/claude-profiles ... (list/create/delete/patch/active/
    initialize/start-oauth/complete-oauth/token/best)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .settings import (
    _sync_env_token_for_active_profile,
    load_profiles,
    save_profiles,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/claude-profiles")
async def get_claude_profiles():
    """Get all Claude profiles."""
    return {"success": True, "data": load_profiles()}


class ClaudeProfile(BaseModel):
    """Claude profile model with frontend-compatible field names.

    Uses camelCase field names with snake_case aliases for backward compatibility
    with existing stored data.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str
    email: str | None = None
    # Frontend expects oauthToken, backend stored token - alias for backward compat
    oauthToken: str | None = Field(None, alias="token")
    # Frontend expects isDefault, backend stored isActive - alias for backward compat
    isDefault: bool = Field(False, alias="isActive")


@router.post("/claude-profiles")
async def save_claude_profile(profile: ClaudeProfile):
    """Save a Claude profile.

    Creates a new profile or updates an existing one. Validates name, email, and token
    to ensure data integrity and prevent duplicates.
    """
    import uuid

    # Validate and sanitize profile name
    if not profile.name or not profile.name.strip():
        return {"success": False, "error": "Profile name cannot be empty"}

    name = profile.name.strip()
    if len(name) < 1 or len(name) > 100:
        return {
            "success": False,
            "error": "Profile name must be between 1 and 100 characters",
        }

    # Sanitize email if provided
    email = profile.email.strip() if profile.email else None
    if email and len(email) > 255:
        return {"success": False, "error": "Email cannot exceed 255 characters"}

    # Validate token if provided (following pattern from set_claude_profile_token)
    token = profile.oauthToken.strip() if profile.oauthToken else None
    if token:
        if len(token) < 20:
            return {"success": False, "error": "Token must be at least 20 characters"}
        if not (token.startswith("sess-") or token.startswith("sk-ant-")):
            return {
                "success": False,
                "error": "Invalid token format. Must start with 'sess-' or 'sk-ant-'",
            }

    data = load_profiles()
    profiles = data.get("profiles", [])

    # Generate ID for new profiles
    is_new = not profile.id
    if is_new:
        profile.id = str(uuid.uuid4())

    # Check for duplicate names (excluding current profile if updating)
    for p in profiles:
        if p.get("name") == name and p.get("id") != profile.id:
            return {
                "success": False,
                "error": f"Profile name '{name}' is already in use",
            }

    # Create profile dict with sanitized values
    # Use frontend-compatible field names (oauthToken, isDefault)
    profile_data = {
        "id": profile.id,
        "name": name,
        "email": email,
        "oauthToken": token,
        "isDefault": profile.isDefault,
    }

    # Update or add profile
    found = False
    for i, p in enumerate(profiles):
        if p.get("id") == profile.id:
            profiles[i] = profile_data
            found = True
            break
    if not found:
        profiles.append(profile_data)

    data["profiles"] = profiles
    save_profiles(data)  # This function sets secure file permissions (0o600)
    return {"success": True, "data": profile_data}


@router.delete("/claude-profiles/{profile_id}")
async def delete_claude_profile(profile_id: str):
    """Delete a Claude profile.

    BUG-4.3-001: Now validates that the profile exists before deletion.
    Returns error if profile is not found.
    """
    # Validate profile_id is not empty/whitespace
    if not profile_id or not profile_id.strip():
        return {"success": False, "error": "Profile ID cannot be empty"}

    profile_id = profile_id.strip()
    data = load_profiles()
    profiles = data.get("profiles", [])

    # Check if profile exists before deletion
    original_count = len(profiles)
    data["profiles"] = [p for p in profiles if p.get("id") != profile_id]

    # BUG-4.3-001: Return error if profile was not found
    if len(data["profiles"]) == original_count:
        return {"success": False, "error": f"Profile {profile_id} not found"}

    # Clear active profile if it was the deleted one
    if data.get("activeProfileId") == profile_id:
        data["activeProfileId"] = None

    save_profiles(data)

    # If no profiles remain, clean up the static OAuth fallback file
    # so ClaudeProvider.detect() doesn't report Claude as still active
    remaining = data.get("profiles", [])
    if not remaining:
        oauth_file = Path.home() / ".claude" / "oauth_token"
        if oauth_file.exists():
            try:
                oauth_file.unlink()
                logging.getLogger(__name__).info(
                    "Removed OAuth fallback file after last Claude profile deleted"
                )
            except OSError as e:
                logging.getLogger(__name__).warning(
                    f"Failed to remove OAuth fallback file: {e}"
                )

    return {"success": True}


class ProfileRename(BaseModel):
    name: str


@router.patch("/claude-profiles/{profile_id}")
async def rename_claude_profile(profile_id: str, update: ProfileRename):
    """Rename a Claude profile with validation."""
    try:
        # Validate name
        if not update.name or not update.name.strip():
            return {"success": False, "error": "Profile name cannot be empty"}

        # Strip whitespace and validate length
        name = update.name.strip()
        if len(name) < 1:
            return {"success": False, "error": "Profile name cannot be empty"}
        if len(name) > 100:
            return {
                "success": False,
                "error": "Profile name cannot exceed 100 characters",
            }

        # Load profiles and update
        data = load_profiles()
        profile_found = False

        # Check for duplicate names (excluding the current profile)
        for p in data.get("profiles", []):
            if p.get("id") != profile_id and p.get("name") == name:
                return {
                    "success": False,
                    "error": f"Profile name '{name}' is already in use",
                }

        # Find and update the profile
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                p["name"] = name
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Save with secure permissions (0o600 set in save_profiles)
        save_profiles(data)
        return {"success": True}
    except Exception:
        logger.exception("Failed to rename Claude profile")
        return {"success": False, "error": "Failed to rename profile"}


class ActiveProfileRequest(BaseModel):
    profileId: str


@router.post("/claude-profiles/active")
async def set_active_claude_profile(request: ActiveProfileRequest):
    """Set the active Claude profile."""
    try:
        logger = logging.getLogger(__name__)
        data = load_profiles()

        # Verify the profile exists
        profile_found = False
        for p in data.get("profiles", []):
            if p.get("id") == request.profileId:
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {request.profileId} not found"}

        data["activeProfileId"] = request.profileId
        save_profiles(data)
        _sync_env_token_for_active_profile(data, request.profileId, logger)
        return {"success": True}
    except Exception:
        logger.exception("Failed to set active Claude profile")
        return {"success": False, "error": "Failed to set active profile"}


@router.post("/claude-profiles/{profile_id}/initialize")
async def initialize_claude_profile(profile_id: str):
    """Initialize a Claude profile.

    Marks a profile as initialized and sets initialization timestamp.
    This can be used after a profile is created to mark it as ready for use.
    """
    try:
        from datetime import datetime

        data = load_profiles()
        profile_found = False

        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                # Mark profile as initialized with timestamp
                p["initialized"] = True
                p["initializedAt"] = datetime.now().isoformat()
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        save_profiles(data)
        return {"success": True}
    except Exception:
        logger.exception("Failed to initialize Claude profile")
        return {"success": False, "error": "Failed to initialize profile"}


def _poll_token_and_save(
    profile_id: str, logger: logging.Logger, mtime_before: float = 0
):
    """
    Poll for Claude OAuth token and save it to the specified profile.

    Checks ~/.claude/.credentials.json for a token written AFTER the OAuth
    flow was initiated (using mtime_before as the baseline).

    This runs in a background thread so the HTTP request can return immediately.

    Args:
        profile_id: The profile to save the token to.
        logger: Logger instance.
        mtime_before: The mtime of credentials.json BEFORE launching the CLI.
            Only tokens from files modified after this time will be accepted.
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"

    for _ in range(90):  # Poll for up to ~3 minutes
        token = None
        try:
            # Only accept tokens from credentials.json that were written
            # AFTER the OAuth flow started (mtime > mtime_before)
            if credentials_path.exists():
                cred_mtime_now = credentials_path.stat().st_mtime
                if cred_mtime_now > mtime_before:
                    cred_data = json.loads(credentials_path.read_text())
                    t = cred_data.get("claudeAiOauth", {}).get("accessToken")
                    if t and t.startswith("sk-ant-oat01-"):
                        token = t

            if token:
                data = load_profiles()
                updated = False
                for p in data.get("profiles", []):
                    if p.get("id") == profile_id:
                        # Only save if the profile doesn't already have this token
                        if p.get("oauthToken") == token:
                            logger.info(
                                f"[Claude OAuth] Profile {profile_id} already has this token, skipping"
                            )
                            return
                        p["oauthToken"] = token
                        p.pop("token", None)  # remove legacy field if present
                        updated = True
                        break
                if updated:
                    save_profiles(data)
                    if data.get("activeProfileId") == profile_id:
                        _sync_env_token_for_active_profile(data, profile_id, logger)
                    logger.info(f"[Claude OAuth] Token saved to profile {profile_id}")
                return
        except Exception as e:  # pragma: no cover - best-effort background
            logger.warning(f"[Claude OAuth] Polling error: {e}")
        time.sleep(2)
    logger.warning(
        f"[Claude OAuth] Token not detected for profile {profile_id} within timeout"
    )


@router.post("/claude-profiles/{profile_id}/start-oauth")
async def start_claude_profile_oauth(profile_id: str):
    """
    Start OAuth token polling for a profile.

    The actual `claude auth login` command runs in the app's interactive
    terminal (frontend creates a PTY terminal and sends the command).
    This endpoint just starts a background poller that watches
    ~/.claude/.credentials.json for a new token and saves it to the profile.
    """
    logger = logging.getLogger(__name__)

    # Validate profile exists
    data = load_profiles()
    profile_exists = any(p.get("id") == profile_id for p in data.get("profiles", []))
    if not profile_exists:
        return {"success": False, "error": f"Profile {profile_id} not found"}

    # Snapshot the credentials file mtime BEFORE the user runs `claude auth login`
    # so the poller only accepts tokens written after this point.
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    mtime_before = credentials_path.stat().st_mtime if credentials_path.exists() else 0

    # Start background poller to save the token once the CLI writes it.
    threading.Thread(
        target=_poll_token_and_save,
        args=(profile_id, logger, mtime_before),
        daemon=True,
    ).start()

    return {
        "success": True,
        "data": {
            "message": "Token polling started. Run 'claude auth login' in the terminal to authenticate.",
        },
    }


@router.post("/claude-profiles/{profile_id}/complete-oauth")
async def complete_claude_profile_oauth(profile_id: str, body: dict):
    """
    Legacy endpoint — no longer needed since auth happens in the terminal.
    Kept for backward compatibility; returns success immediately.
    """
    return {
        "success": True,
        "data": {
            "message": "Auth is handled via the terminal. Token will be saved automatically."
        },
    }


class SetTokenRequest(BaseModel):
    token: str
    email: str | None = None


@router.post("/claude-profiles/{profile_id}/token")
async def set_claude_profile_token(profile_id: str, request: SetTokenRequest):
    """Set token for a Claude profile with validation and secure storage."""
    try:
        logger = logging.getLogger(__name__)
        # Validate token
        if not request.token or not request.token.strip():
            return {"success": False, "error": "Token cannot be empty"}

        # Validate token length (Claude tokens are typically > 20 characters)
        if len(request.token) < 20:
            return {
                "success": False,
                "error": "Token appears invalid. Must be at least 20 characters.",
            }

        # Validate token format (Claude session tokens start with 'sess-' or API keys with 'sk-ant-')
        token = request.token.strip()
        if not (token.startswith("sess-") or token.startswith("sk-ant-")):
            return {
                "success": False,
                "error": "Invalid Claude token format. Must start with 'sess-' or 'sk-ant-'",
            }

        # Load profiles and update
        data = load_profiles()
        profile_found = False

        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                # Use frontend-compatible field name
                p["oauthToken"] = token
                # Remove old field name if present (migration)
                p.pop("token", None)
                if request.email:
                    p["email"] = request.email.strip()
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Save with secure permissions (0o600 set in save_profiles)
        save_profiles(data)
        _sync_env_token_for_active_profile(data, data.get("activeProfileId"), logger)
        return {"success": True}
    except Exception:
        logger.exception("Failed to set Claude profile token")
        return {"success": False, "error": "Failed to set profile token"}


@router.get("/claude-profiles/best")
async def get_best_available_profile(exclude: str | None = None):
    """Get the best available Claude profile with a valid token.

    BUG-4.3-003: Now validates that the profile has a token before returning.
    Prioritizes the active profile if it has a valid token and is not excluded.
    """
    data = load_profiles()
    active_id = data.get("activeProfileId")

    # Filter to usable profiles (has token, not excluded)
    # Check both oauthToken (new field name) and token (old field name)
    usable = [
        p
        for p in data.get("profiles", [])
        if p.get("id") != exclude and (p.get("oauthToken") or p.get("token"))
    ]

    if not usable:
        return {"success": True, "data": None}

    # Prefer active profile if it's in the usable list
    for p in usable:
        if p.get("id") == active_id:
            return {"success": True, "data": p}

    # Return first usable profile
