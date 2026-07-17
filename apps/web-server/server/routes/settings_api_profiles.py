"""API-profile settings endpoints — extracted from routes/settings.py (#360).

A focused sub-router for OpenAI-compatible API-profile management, carved out of
routes/settings.py. Behaviour and paths unchanged; main.py mounts it under the
same /api/settings prefix. Shared helpers/models still live in
routes/settings.py and are imported here.

    /api/settings/api-profiles ... (list/create/update/delete/active/test/
    discover-models)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import get_settings
from ..paths import write_secret_file

router = APIRouter()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# API Profiles (OpenAI-compatible endpoints)
# --------------------------------------------------------------------------


def get_api_profiles_file() -> Path:
    """Get path to the API profiles file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "api-profiles.json"


def load_api_profiles() -> dict:
    """Load API profiles."""
    profiles_file = get_api_profiles_file()
    if profiles_file.exists():
        try:
            return json.loads(profiles_file.read_text())
        except json.JSONDecodeError:
            pass
    return {"profiles": [], "activeProfileId": None}


def save_api_profiles(data: dict) -> None:
    """Save API profiles.

    Security: written 0600 from creation (owner read/write only) to protect the
    API keys and tokens in the profiles. Same at-rest posture as
    ``settings.save_profiles`` — see its docstring for the #663 decision.
    """
    write_secret_file(get_api_profiles_file(), json.dumps(data, indent=2))


@router.get("/api-profiles")
async def get_api_profiles():
    """Get all API profiles."""
    return {"success": True, "data": load_api_profiles()}


@router.post("/api-profiles")
async def save_api_profile(profile: dict):
    """Save an API profile."""
    import uuid

    data = load_api_profiles()
    if not profile.get("id"):
        profile["id"] = str(uuid.uuid4())

    profiles = data.get("profiles", [])
    found = False
    for i, p in enumerate(profiles):
        if p.get("id") == profile["id"]:
            profiles[i] = profile
            found = True
            break
    if not found:
        profiles.append(profile)

    data["profiles"] = profiles
    save_api_profiles(data)
    return {"success": True, "data": profile}


class ApiProfileModels(BaseModel):
    """Optional model mappings for API profile."""

    default: str | None = Field(
        None, description="Default model (maps to ANTHROPIC_MODEL)"
    )
    haiku: str | None = Field(
        None, description="Haiku model (maps to ANTHROPIC_DEFAULT_HAIKU_MODEL)"
    )
    sonnet: str | None = Field(
        None, description="Sonnet model (maps to ANTHROPIC_DEFAULT_SONNET_MODEL)"
    )
    opus: str | None = Field(
        None, description="Opus model (maps to ANTHROPIC_DEFAULT_OPUS_MODEL)"
    )


class ApiProfileUpdate(BaseModel):
    """Model for updating API profile configuration.

    All fields are optional to support partial updates. Only provided fields
    will be updated in the profile.
    """

    name: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        description="Profile name (1-100 characters)",
    )
    baseUrl: str | None = Field(None, min_length=1, description="API endpoint URL")
    apiKey: str | None = Field(
        None, min_length=20, description="API key (minimum 20 characters)"
    )
    models: ApiProfileModels | None = Field(None, description="Optional model mappings")


@router.put("/api-profiles/{profile_id}")
async def update_api_profile(profile_id: str, profile_update: ApiProfileUpdate):
    """Update an API profile.

    Supports partial updates - only provided fields will be updated.
    Validates all inputs and maintains secure file storage.

    Validation:
    - name: 1-100 characters, no duplicates
    - baseUrl: Non-empty, valid URL format (http:// or https://)
    - apiKey: Minimum 20 characters
    - Automatically updates updatedAt timestamp
    - Maintains secure file permissions (0o600)
    """
    try:
        # Validate profile_id
        if not profile_id or not profile_id.strip():
            return {"success": False, "error": "Profile ID cannot be empty"}

        profile_id = profile_id.strip()

        # Load existing profiles
        data = load_api_profiles()
        profiles = data.get("profiles", [])

        # Find the profile to update
        profile_index = None
        current_profile = None
        for i, p in enumerate(profiles):
            if p.get("id") == profile_id:
                profile_index = i
                current_profile = p
                break

        if profile_index is None:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Get update data (exclude None values for partial update)
        update_data = profile_update.model_dump(exclude_none=True)

        # Validate and sanitize name if provided
        if "name" in update_data:
            name = update_data["name"].strip()
            if not name:
                return {"success": False, "error": "Profile name cannot be empty"}
            if len(name) < 1 or len(name) > 100:
                return {
                    "success": False,
                    "error": "Profile name must be between 1 and 100 characters",
                }

            # Check for duplicate names (excluding current profile)
            for p in profiles:
                if (
                    p.get("id") != profile_id
                    and p.get("name", "").strip().lower() == name.lower()
                ):
                    return {
                        "success": False,
                        "error": f"Profile name '{name}' is already in use",
                    }

            update_data["name"] = name

        # Validate and sanitize baseUrl if provided
        if "baseUrl" in update_data:
            base_url = update_data["baseUrl"].strip()
            if not base_url:
                return {"success": False, "error": "Base URL cannot be empty"}
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                return {
                    "success": False,
                    "error": "Base URL must start with http:// or https://",
                }

            update_data["baseUrl"] = base_url

        # Validate and sanitize apiKey if provided
        if "apiKey" in update_data:
            api_key = update_data["apiKey"].strip()
            if not api_key:
                return {"success": False, "error": "API key cannot be empty"}
            if len(api_key) < 20:
                return {
                    "success": False,
                    "error": "API key must be at least 20 characters",
                }

            update_data["apiKey"] = api_key

        # Update timestamp
        import time

        update_data["updatedAt"] = int(
            time.time() * 1000
        )  # Unix timestamp in milliseconds

        # Merge updates into current profile (preserving id and createdAt)
        updated_profile = {**current_profile, **update_data, "id": profile_id}

        # Preserve createdAt if it exists
        if "createdAt" in current_profile:
            updated_profile["createdAt"] = current_profile["createdAt"]

        # Update profile in list
        profiles[profile_index] = updated_profile
        data["profiles"] = profiles

        # Save with secure permissions (0o600 - owner read/write only)
        save_api_profiles(data)

        return {"success": True, "data": updated_profile}

    except Exception:
        logger.exception("Failed to update API profile")
        return {"success": False, "error": "Failed to update API profile"}


@router.delete("/api-profiles/{profile_id}")
async def delete_api_profile(profile_id: str):
    """
    Delete an API profile.

    Validates:
    - Profile ID is not empty
    - Profile exists in api-profiles.json
    - Profile is NOT the currently active profile (deletion prevented)

    Args:
        profile_id: The ID of the profile to delete

    Returns:
        Success response on deletion, or error response with details

    Security:
        Uses save_api_profiles() which sets secure file permissions (0o600)
    """
    try:
        # Validate profile_id is not empty
        if not profile_id or not profile_id.strip():
            return {"success": False, "error": "Profile ID cannot be empty"}

        # Strip whitespace from profile_id
        profile_id = profile_id.strip()

        # Load current API profiles
        data = load_api_profiles()

        # Find the profile to delete and get its name for error messages
        profile_to_delete = None
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                profile_to_delete = p
                break

        # Check if profile exists
        if not profile_to_delete:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # CRITICAL: Prevent deletion of the active profile
        # This ensures users don't accidentally delete the profile they're currently using
        active_profile_id = data.get("activeProfileId")
        if active_profile_id and active_profile_id == profile_id:
            profile_name = profile_to_delete.get("name", profile_id)
            return {
                "success": False,
                "error": f"Cannot delete active profile '{profile_name}'. Please switch to a different profile first.",
            }

        # Remove the profile from the profiles array
        data["profiles"] = [
            p for p in data.get("profiles", []) if p.get("id") != profile_id
        ]

        # Save updated profiles (with secure 0o600 permissions)
        save_api_profiles(data)

        return {
            "success": True,
            "message": "Profile deleted successfully",
            "deletedProfileId": profile_id,
        }

    except Exception:
        logger.exception("Failed to delete API profile")
        return {"success": False, "error": "Failed to delete API profile"}


@router.post("/api-profiles/active")
async def set_active_api_profile(request: dict):
    """Set the active API profile."""
    try:
        profile_id = request.get("profileId")

        if not profile_id:
            return {"success": False, "error": "profileId is required"}

        data = load_api_profiles()

        # Verify the profile exists
        profile_found = False
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        data["activeProfileId"] = profile_id
        save_api_profiles(data)
        return {"success": True}
    except Exception:
        logger.exception("Failed to set active API profile")
        return {"success": False, "error": "Failed to set active API profile"}


class TestConnectionRequest(BaseModel):
    baseUrl: str
    apiKey: str


@router.post("/api-profiles/test")
async def test_api_connection(request: TestConnectionRequest):
    """Test connection to an API endpoint."""
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{request.baseUrl}/models",
            headers={"Authorization": f"Bearer {request.apiKey}"},
        )
        urllib.request.urlopen(req, timeout=10)
        return {"success": True, "data": {"connected": True}}
    except Exception:
        logger.exception("API connection test failed")
        return {"success": False, "error": "Connection test failed"}


@router.post("/api-profiles/discover-models")
async def discover_api_models(request: TestConnectionRequest):
    """Discover available models from an API endpoint."""
    import json as json_module
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{request.baseUrl}/models",
            headers={"Authorization": f"Bearer {request.apiKey}"},
        )
        response = urllib.request.urlopen(req, timeout=10)
        data = json_module.loads(response.read().decode())
        models = [m.get("id") for m in data.get("data", [])]
        return {"success": True, "data": models}
    except Exception:
        logger.exception("Failed to discover API models")
        return {"success": False, "error": "Failed to discover models"}
