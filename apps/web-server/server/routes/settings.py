"""
Application settings routes.

Handles reading and writing application configuration.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from ..paths import write_secret_file

# --------------------------------------------------------------------------
# Type Definitions for Validation
# --------------------------------------------------------------------------

# BUG-4.1-005: Theme must be one of these values
ThemeType = Literal["light", "dark", "system"]

# Color theme — Ocean only
ColorThemeType = Literal["ocean"]

# BUG-4.1-011: Memory embedding provider must be one of these values
MemoryEmbeddingProviderType = Literal[
    "openai", "voyage", "azure_openai", "ollama", "google", "openrouter"
]

from ..config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class AppSettings(BaseModel):
    """Application settings model."""

    # Allow both camelCase field names and snake_case aliases for backward compatibility
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # General - with proper type validation
    # BUG-4.1-005: Validate theme against allowed values
    theme: ThemeType = Field("dark", description="UI theme (dark/light/system)")
    # BUG-4.1-006: Validate colorTheme against allowed values
    colorTheme: ColorThemeType | None = Field("ocean", description="Color theme (default/dusk/lime/ocean/retro/neo/forest)")
    language: str = Field("en", description="UI language code")
    # BUG-4.1-008: uiScale with min/max validation (75-200)
    uiScale: int | None = Field(125, ge=75, le=200, description="UI scale percentage (75-200)")

    @field_validator("theme", mode="before")
    @classmethod
    def validate_theme(cls, v):
        """Validate and normalize theme value for backward compatibility."""
        if v is None:
            return "dark"
        valid_themes = ["light", "dark", "system"]
        if v not in valid_themes:
            # Fall back to dark for invalid values (backward compatibility)
            return "dark"
        return v

    @field_validator("colorTheme", mode="before")
    @classmethod
    def validate_color_theme(cls, v):
        """Normalize any color theme value to 'ocean' (sole supported theme)."""
        return "ocean"

    @field_validator("uiScale", mode="before")
    @classmethod
    def validate_ui_scale(cls, v):
        """Validate and clamp uiScale to valid range for backward compatibility."""
        if v is None:
            return 125
        try:
            scale = int(v)
            # Clamp to valid range instead of rejecting (backward compatibility)
            return max(75, min(200, scale))
        except (TypeError, ValueError):
            return 125

    # Claude settings - using camelCase with snake_case aliases for backward compatibility
    defaultModel: str = Field(
        "claude-sonnet-4-5-20250929",
        alias="default_model",
        description="Default Claude model for tasks",
    )
    agentFramework: str | None = Field("claude-code", description="Agent framework to use")
    thinkingLevel: str = Field(
        "extended",
        alias="thinking_level",
        description="Thinking level (none, standard, extended)",
    )
    maxThinkingTokens: int | None = Field(
        None,
        alias="max_thinking_tokens",
        description="Max thinking tokens (None for unlimited)",
    )
    selectedAgentProfile: str | None = Field(None, description="Selected agent profile ID")

    # Task execution - using camelCase with snake_case aliases
    autoContinue: bool = Field(
        True,
        alias="auto_continue",
        description="Automatically continue to next phase after spec creation",
    )
    autoQa: bool = Field(
        True,
        alias="auto_qa",
        description="Automatically run QA after implementation",
    )
    # Terminal - using camelCase with snake_case aliases
    defaultShell: str = Field("/bin/bash", alias="default_shell", description="Default shell for terminals")
    terminalFontSize: int = Field(14, alias="terminal_font_size", description="Terminal font size")
    autoNameTerminals: bool = Field(True, description="Auto-generate terminal names")

    # Developer tools
    preferredIDE: str | None = Field(None, description="Preferred IDE")
    customIDEPath: str | None = Field(None, description="Custom IDE path")
    preferredTerminal: str | None = Field(None, description="Preferred terminal")
    customTerminalPath: str | None = Field(None, description="Custom terminal path")

    # Integrations - using camelCase with snake_case aliases
    githubEnabled: bool = Field(False, alias="github_enabled", description="Enable GitHub integration")

    # Memory - using camelCase with snake_case aliases
    graphitiEnabled: bool = Field(True, alias="graphiti_enabled", description="Enable Graphiti memory")
    memoryEnabled: bool | None = Field(None, description="Enable memory system")
    # BUG-4.1-011: Validate memoryEmbeddingProvider against allowed values
    memoryEmbeddingProvider: MemoryEmbeddingProviderType | None = Field(
        None, description="Memory embedding provider (openai/voyage/azure_openai/ollama/google/openrouter)"
    )

    @field_validator("memoryEmbeddingProvider", mode="before")
    @classmethod
    def validate_memory_embedding_provider(cls, v):
        """Validate memoryEmbeddingProvider for backward compatibility."""
        if v is None:
            return None
        valid_providers = ["openai", "voyage", "azure_openai", "ollama", "google", "openrouter"]
        if v not in valid_providers:
            # Return None for invalid values (backward compatibility)
            return None
        return v

    # Paths
    autoBuildPath: str | None = Field(
        None,
        description="Path to Magestic AI backend (apps/backend directory)",
    )
    autoUpdateAutoBuild: bool = Field(True, description="Auto-update Magestic AI source")

    # Global API keys
    globalClaudeOAuthToken: str | None = Field(None, description="Global Claude OAuth token")
    globalOpenAIApiKey: str | None = Field(None, description="Global OpenAI API key")
    globalAnthropicApiKey: str | None = Field(None, description="Global Anthropic API key")

    # Onboarding
    onboardingCompleted: bool | None = Field(None, description="Whether onboarding is completed")

    # Updates
    betaUpdates: bool | None = Field(False, description="Opt into beta updates")

    # Email Notification OAuth Credentials (app-level, not per-user)
    emailMicrosoftClientId: str | None = Field(None, description="Microsoft OAuth Client ID for email notifications")
    emailMicrosoftClientSecret: str | None = Field(None, description="Microsoft OAuth Client Secret for email notifications")
    emailGoogleClientId: str | None = Field(None, description="Google OAuth Client ID for email notifications")
    emailGoogleClientSecret: str | None = Field(None, description="Google OAuth Client Secret for email notifications")

    # LLM Provider Settings (for AI features: changelog, insights)
    llmProvider: Literal["ollama", "anthropic", "openai"] | None = Field(
        default="ollama",
        alias="llmProvider",
        validation_alias=AliasChoices("llmProvider", "llm_provider")
    )

    llmOllamaBaseUrl: str | None = Field(
        default="http://localhost:11434",
        alias="llmOllamaBaseUrl",
        validation_alias=AliasChoices("llmOllamaBaseUrl", "llm_ollama_base_url")
    )

    llmOllamaModel: str | None = Field(
        default="qwen3-30b-local:latest",
        alias="llmOllamaModel",
        validation_alias=AliasChoices("llmOllamaModel", "llm_ollama_model")
    )

    llmAnthropicModel: str | None = Field(
        default="claude-sonnet-4-5-20250929",
        alias="llmAnthropicModel",
        validation_alias=AliasChoices("llmAnthropicModel", "llm_anthropic_model")
    )

    llmOpenaiModel: str | None = Field(
        default="gpt-4o",
        alias="llmOpenaiModel",
        validation_alias=AliasChoices("llmOpenaiModel", "llm_openai_model")
    )

    llmOpenaiBaseUrl: str | None = Field(
        default=None,
        alias="llmOpenaiBaseUrl",
        validation_alias=AliasChoices("llmOpenaiBaseUrl", "llm_openai_base_url")
    )

    @field_validator("llmProvider", mode="before")
    @classmethod
    def validate_llm_provider(cls, v: Any) -> str | None:
        """Validate LLM provider."""
        if v is None:
            return "ollama"  # Default to Ollama
        if isinstance(v, str):
            v = v.lower()
            if v in ["ollama", "anthropic", "openai"]:
                return v
        return "ollama"  # Fallback

    # Phase-specific model overrides — supports provider-prefixed IDs like
    # 'ollama:llama3', 'openai_compat:mistral-7b', or plain shorthand 'opus'
    phaseModels: dict | None = Field(
        None,
        description=(
            "Per-phase model overrides, e.g. {spec: 'opus', coding: 'ollama:llama3'}. "
            "Valid keys: spec, planning, coding, qa, qa_fixer. "
            "Values may be any non-empty string (provider-prefixed or plain shorthand)."
        ),
    )

    @field_validator("phaseModels", mode="before")
    @classmethod
    def validate_phase_models(cls, v: Any) -> dict | None:
        """Validate phaseModels keys and values.

        Accepted keys: spec, planning, coding, qa, qa_fixer.
        Values must be non-empty strings (any provider-prefixed model ID is allowed).
        Unknown keys and None/empty values are silently dropped for backward compatibility.
        """
        if v is None:
            return None
        if not isinstance(v, dict):
            return None
        valid_keys = {"spec", "planning", "coding", "qa", "qa_fixer"}
        cleaned: dict = {}
        for key, value in v.items():
            if key not in valid_keys:
                # Skip unknown keys rather than raising, for forward compatibility
                continue
            if not isinstance(value, str) or not value.strip():
                # Skip non-string or empty values
                continue
            cleaned[key] = value
        return cleaned if cleaned else None


class SettingsUpdate(BaseModel):
    """Model for partial settings update."""

    # Allow both camelCase field names and snake_case aliases for backward compatibility
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    theme: str | None = None
    colorTheme: str | None = None
    language: str | None = None
    uiScale: int | None = None
    # Using camelCase with snake_case aliases for backward compatibility
    defaultModel: str | None = Field(None, alias="default_model")
    agentFramework: str | None = None
    thinkingLevel: str | None = Field(None, alias="thinking_level")
    maxThinkingTokens: int | None = Field(None, alias="max_thinking_tokens")
    selectedAgentProfile: str | None = None
    autoContinue: bool | None = Field(None, alias="auto_continue")
    autoQa: bool | None = Field(None, alias="auto_qa")
    defaultShell: str | None = Field(None, alias="default_shell")
    terminalFontSize: int | None = Field(None, alias="terminal_font_size")
    autoNameTerminals: bool | None = None
    preferredIDE: str | None = None
    customIDEPath: str | None = None
    preferredTerminal: str | None = None
    customTerminalPath: str | None = None
    githubEnabled: bool | None = Field(None, alias="github_enabled")
    graphitiEnabled: bool | None = Field(None, alias="graphiti_enabled")
    memoryEnabled: bool | None = None
    memoryEmbeddingProvider: str | None = None
    autoBuildPath: str | None = None
    autoUpdateAutoBuild: bool | None = None
    globalClaudeOAuthToken: str | None = None
    globalOpenAIApiKey: str | None = None
    globalAnthropicApiKey: str | None = None
    onboardingCompleted: bool | None = None
    betaUpdates: bool | None = None
    emailMicrosoftClientId: str | None = None
    emailMicrosoftClientSecret: str | None = None
    llmProvider: str | None = None
    llmOllamaBaseUrl: str | None = None
    llmOllamaModel: str | None = None
    llmAnthropicModel: str | None = None
    llmOpenaiModel: str | None = None
    llmOpenaiBaseUrl: str | None = None
    phaseModels: dict | None = None


class UpdateApiKeyRequest(BaseModel):
    """Request model for updating API keys."""
    keyType: str = Field(..., description="Type of API key (anthropic, openai, claude)")
    keyValue: str = Field(..., description="The API key value")
    saveToEnv: bool = Field(True, description="Whether to save to .env file")


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------


def get_settings_file() -> Path:
    """Get path to the settings file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "settings.json"


def load_app_settings() -> AppSettings:
    """Load application settings from disk."""
    settings_file = get_settings_file()
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text())
            return AppSettings(**data)
        except (json.JSONDecodeError, TypeError):
            pass
    return AppSettings()


def save_app_settings(settings: AppSettings) -> None:
    """Save application settings to disk."""
    settings_file = get_settings_file()
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(settings.model_dump_json(indent=2))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("", response_model=AppSettings)
async def get_app_settings():
    """Get current application settings."""
    return load_app_settings()


@router.put("", response_model=AppSettings)
async def update_app_settings(update: SettingsUpdate):
    """Update application settings (partial update)."""
    current = load_app_settings()

    # Apply updates
    update_dict = update.model_dump(exclude_unset=True)
    current_dict = current.model_dump()
    current_dict.update(update_dict)

    updated = AppSettings(**current_dict)
    save_app_settings(updated)

    return updated


@router.post("/reset", response_model=AppSettings)
async def reset_app_settings():
    """Reset application settings to defaults."""
    default = AppSettings()
    save_app_settings(default)
    return default


@router.get("/token")
async def get_api_token():
    """Get the current API token (for display to user)."""
    settings = get_settings()
    return {
        "token": settings.API_TOKEN,
        "note": "Use this token in the Authorization header: Bearer <token>",
    }


@router.post("/token/regenerate")
async def regenerate_api_token():
    """Regenerate the API token."""
    import secrets

    from ..paths import get_data_file

    settings = get_settings()
    token_file = get_data_file(".token")

    # Generate new token
    new_token = secrets.token_urlsafe(32)
    token_file.write_text(new_token)
    token_file.chmod(0o600)

    # Update settings (note: requires server restart to take effect)
    return {
        "token": new_token,
        "note": "Server restart required for new token to take effect",
    }


@router.post("/api-key")
async def update_api_key(request: UpdateApiKeyRequest):
    """Update global API key and optionally save to .env file.

    This endpoint allows updating API keys for various services (Anthropic, OpenAI, Claude)
    and saving them securely to a .env file with proper validation and permissions.
    """
    from pathlib import Path

    # Validate key type
    valid_key_types = ["anthropic", "openai", "claude"]
    if request.keyType.lower() not in valid_key_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid keyType. Must be one of: {', '.join(valid_key_types)}"
        )

    # Validate key format (basic validation)
    if not request.keyValue or len(request.keyValue) < 20:
        raise HTTPException(
            status_code=400,
            detail="API key appears invalid. Must be at least 20 characters."
        )

    # Additional format validation based on key type
    key_type = request.keyType.lower()
    if key_type == "anthropic" and not request.keyValue.startswith("sk-ant-"):
        raise HTTPException(
            status_code=400,
            detail="Anthropic API keys must start with 'sk-ant-'"
        )
    elif key_type == "openai" and not request.keyValue.startswith("sk-"):
        raise HTTPException(
            status_code=400,
            detail="OpenAI API keys must start with 'sk-'"
        )
    elif key_type == "claude" and not (request.keyValue.startswith("sk-ant-") or request.keyValue.startswith("sess-")):
        raise HTTPException(
            status_code=400,
            detail="Claude API keys must start with 'sk-ant-' or 'sess-'"
        )

    try:
        # Update in-memory settings
        current = load_app_settings()

        # Map key type to settings field
        key_field_map = {
            "anthropic": "globalAnthropicApiKey",
            "openai": "globalOpenAIApiKey",
            "claude": "globalClaudeOAuthToken"
        }

        field_name = key_field_map[key_type]
        setattr(current, field_name, request.keyValue)

        # Save to settings.json
        save_app_settings(current)

        # Optionally save to .env file with secure permissions
        if request.saveToEnv:
            settings = get_settings()
            env_path = Path(settings.PROJECTS_DATA_DIR) / ".env"

            # Read existing .env or start fresh
            existing = {}
            if env_path.exists():
                for line in env_path.read_text().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        existing[key.strip()] = value.strip()

            # Update with new key
            env_key_map = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "claude": "CLAUDE_API_KEY"
            }

            existing[env_key_map[key_type]] = request.keyValue

            # Write back with secure permissions
            env_path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(f"{k}={v}" for k, v in existing.items())
            env_path.write_text(content)

            # Set secure file permissions (owner read/write only)
            env_path.chmod(0o600)

        return {
            "success": True,
            "message": f"{key_type.title()} API key updated successfully",
            "savedToEnv": request.saveToEnv
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update API key: {str(e)}"
        )


# --------------------------------------------------------------------------
# Tab State
# --------------------------------------------------------------------------

def get_tab_state_file() -> Path:
    """Get path to the tab state file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "tab-state.json"


@router.get("/tab-state")
async def get_tab_state():
    """Get saved tab state."""
    tab_file = get_tab_state_file()
    if tab_file.exists():
        try:
            data = json.loads(tab_file.read_text())
            return {"success": True, "data": data}
        except json.JSONDecodeError:
            pass
    return {"success": True, "data": {"tabs": [], "activeTabId": None}}


@router.put("/tab-state")
async def save_tab_state(state: dict):
    """Save tab state."""
    try:
        tab_file = get_tab_state_file()
        tab_file.parent.mkdir(parents=True, exist_ok=True)
        tab_file.write_text(json.dumps(state, indent=2))
        return {"success": True}
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save tab state: {str(e)}"
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tab state data: {str(e)}"
        )


# --------------------------------------------------------------------------
# Claude Profiles
# --------------------------------------------------------------------------

def get_profiles_file() -> Path:
    """Get path to the Claude profiles file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "claude-profiles.json"


def normalize_profile_fields(profile: dict) -> dict:
    """Normalize profile field names to frontend-compatible camelCase.

    Converts old field names to new ones for backward compatibility:
    - token -> oauthToken
    - isActive -> isDefault
    """
    result = profile.copy()

    # Migrate token -> oauthToken
    if "token" in result and "oauthToken" not in result:
        result["oauthToken"] = result.pop("token")
    elif "token" in result:
        result.pop("token")  # Remove old field if new one exists

    # Migrate isActive -> isDefault
    if "isActive" in result and "isDefault" not in result:
        result["isDefault"] = result.pop("isActive")
    elif "isActive" in result:
        result.pop("isActive")  # Remove old field if new one exists

    return result


def load_profiles() -> dict:
    """Load Claude profiles with field name normalization."""
    profiles_file = get_profiles_file()
    if profiles_file.exists():
        try:
            data = json.loads(profiles_file.read_text())
            # Normalize field names for backward compatibility
            if "profiles" in data:
                data["profiles"] = [
                    normalize_profile_fields(p) for p in data["profiles"]
                ]
            return data
        except json.JSONDecodeError:
            pass
    return {"profiles": [], "activeProfileId": None}


def save_profiles(data: dict) -> None:
    """Save Claude profiles with secure file permissions.

    Profiles hold ``CLAUDE_CODE_OAUTH_TOKEN`` values and are stored as plaintext
    JSON at 0600. That is a deliberate decision (#663, CodeQL alert #711
    ``py/clear-text-storage-sensitive-data``), not an oversight:

    - The consumer of these tokens is the Claude Agent SDK, which requires the
      cleartext value in the process environment. Any at-rest encryption must be
      reversible by this same process, on this same host.
    - Encrypting with a key that also lives on the host moves the secret; it does
      not protect it. An attacker who can read this file already has our uid, and
      with it the key and the decrypted value. That is theatre, so we do not do it.
    - The ``EncryptedString``/KMS layer used for DB credentials is only meaningful
      because its key is external to the database. It would buy real
      defence-in-depth here too, but only against threats that read the data
      volume without running as us (PVC snapshots, backups) — which is not the
      current single-tenant deployment's model.

    Revisit if tokens can outlive the pod: if the data PVC gets snapshotted or
    backed up, or the deployment goes multi-tenant, route profiles through the
    KMS layer (option 2 in #663) rather than widening this comment.

    What is *not* acceptable at any threat model is a window where the file is
    readable by others, hence ``write_secret_file`` rather than write-then-chmod.
    """
    write_secret_file(get_profiles_file(), json.dumps(data, indent=2))


def _sync_env_token_for_active_profile(
    data: dict,
    profile_id: str | None,
    logger: logging.Logger,
) -> None:
    """Update CLAUDE_CODE_OAUTH_TOKEN to match the active profile token."""
    if not profile_id:
        return

    token = None
    for profile in data.get("profiles", []):
        if profile.get("id") == profile_id:
            token = profile.get("oauthToken") or profile.get("token")
            break

    if token:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
        logger.info("[Claude Profiles] Updated CLAUDE_CODE_OAUTH_TOKEN for active profile")
    else:
        logger.warning(
            "[Claude Profiles] Active profile has no token; CLAUDE_CODE_OAUTH_TOKEN not updated"
        )


# --------------------------------------------------------------------------
# Auto-Switch Settings
# --------------------------------------------------------------------------

class AutoSwitchSettingsUpdate(BaseModel):
    """Model for updating auto-switch settings."""
    enabled: bool | None = None
    threshold: int | None = Field(None, ge=0, le=100, description="Usage threshold percentage (0-100)")
    proactiveSwapEnabled: bool | None = None  # Proactive monitoring toggle
    autoSwitchOnRateLimit: bool | None = None  # Reactive recovery on rate limit/errors
    usageCheckInterval: int | None = Field(None, ge=0, description="Usage polling interval in ms (0 disables)")
    sessionThreshold: int | None = Field(None, ge=0, le=100, description="Percent threshold for session usage")
    weeklyThreshold: int | None = Field(None, ge=0, le=100, description="Percent threshold for weekly usage")


def get_auto_switch_file() -> Path:
    """Get path to the auto-switch settings file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "auto-switch.json"


@router.get("/auto-switch")
async def get_auto_switch_settings():
    """Get auto-switch settings."""
    auto_switch_file = get_auto_switch_file()
    if auto_switch_file.exists():
        try:
            data = json.loads(auto_switch_file.read_text())
            return {"success": True, "data": data}
        except json.JSONDecodeError:
            pass
    return {
        "success": True,
        "data": {
            "enabled": False,
            "threshold": 80,
            "proactiveSwapEnabled": True,
            "autoSwitchOnRateLimit": False,
            "usageCheckInterval": 30000,
            "sessionThreshold": 95,
            "weeklyThreshold": 99,
        },
    }


@router.patch("/auto-switch")
async def update_auto_switch_settings(settings_update: AutoSwitchSettingsUpdate):
    """Update auto-switch settings with validation and secure storage.
    
    Updates the auto-switch configuration which controls automatic profile switching
    based on usage thresholds. Settings are stored in auto-switch.json with secure
    file permissions.
    
    Args:
        settings_update: Auto-switch settings update with optional enabled and threshold fields
        
    Returns:
        Success response with updated settings or error details
    """
    try:
        auto_switch_file = get_auto_switch_file()
        # Load current settings (with defaults)
        current = {
            "enabled": False,
            "threshold": 80,
            "proactiveSwapEnabled": True,
            "autoSwitchOnRateLimit": False,
            "usageCheckInterval": 30000,
            "sessionThreshold": 95,
            "weeklyThreshold": 99,
        }
        if auto_switch_file.exists():
            try:
                current = json.loads(auto_switch_file.read_text())
            except json.JSONDecodeError:
                logger.exception("Failed to parse existing auto-switch.json")
                return {
                    "success": False,
                    "error": "Failed to parse existing auto-switch settings"
                }
        
        # Update with new values (only non-None values from Pydantic model)
        update_dict = settings_update.model_dump(exclude_none=True)
        current.update(update_dict)
        
        # Ensure parent directory exists
        auto_switch_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Write updated settings with pretty formatting
        auto_switch_file.write_text(json.dumps(current, indent=2))
        
        # Set secure file permissions (owner read/write only)
        # Following security pattern from save_profiles() and other Phase 2/3 endpoints
        auto_switch_file.chmod(0o600)
        
        return {"success": True, "data": current}
        
    except Exception:
        logger.exception("Failed to update auto-switch settings")
        return {"success": False, "error": "Failed to update auto-switch settings"}


class RetryWithProfileRequest(BaseModel):
    """Request model for retrying with a different profile."""
    profileId: str = Field(..., min_length=1, description="ID of the profile to switch to")
    reason: str | None = Field(None, description="Reason for the switch (e.g., 'rate_limit', 'error')")
    operationContext: dict | None = Field(None, description="Optional context about the failed operation")


@router.post("/retry-with-profile")
async def retry_with_profile(request: RetryWithProfileRequest):
    """Switch to a different Claude profile and prepare for operation retry.

    This endpoint facilitates profile switching when an operation fails due to
    rate limits or other profile-specific issues. It switches the active profile
    and returns information needed for the user/frontend to retry the operation.

    Common use case:
    1. User hits rate limit with current profile
    2. Frontend calls this endpoint with a different profileId
    3. Profile is switched
    4. User manually retries the operation (or frontend auto-retries)

    Args:
        request: Profile switch request with profileId, optional reason, and operation context

    Returns:
        Success response with profile switch details including:
        - previousProfileId: The profile that was active before the switch
        - newProfileId: The newly activated profile
        - profileName: Name of the new profile for display
        - reason: Switch reason if provided
    """
    try:
        logger = logging.getLogger(__name__)
        # Validate profileId is not empty/whitespace
        profile_id = request.profileId.strip() if request.profileId else ""
        if not profile_id:
            return {"success": False, "error": "profileId cannot be empty"}

        # Load profiles
        data = load_profiles()

        # Store previous active profile for return data
        previous_profile_id = data.get("activeProfileId")

        # Check if trying to switch to the same profile
        if previous_profile_id == profile_id:
            # Find profile name for better error message
            profile_name = None
            for p in data.get("profiles", []):
                if p.get("id") == profile_id:
                    profile_name = p.get("name", "Unknown")
                    break
            return {
                "success": False,
                "error": f"Profile '{profile_name or profile_id}' is already active"
            }

        # Verify the target profile exists and get profile details
        target_profile = None
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                target_profile = p
                break

        if not target_profile:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Get previous profile name for logging/response
        previous_profile_name = None
        if previous_profile_id:
            for p in data.get("profiles", []):
                if p.get("id") == previous_profile_id:
                    previous_profile_name = p.get("name")
                    break

        # Set the new profile as active
        data["activeProfileId"] = profile_id

        # Save profiles with secure permissions (via save_profiles function)
        save_profiles(data)
        _sync_env_token_for_active_profile(data, profile_id, logger)

        # Build response with comprehensive information
        response = {
            "success": True,
            "previousProfileId": previous_profile_id,
            "previousProfileName": previous_profile_name,
            "newProfileId": profile_id,
            "profileName": target_profile.get("name"),
            "profileEmail": target_profile.get("email"),
        }

        # Include reason if provided
        if request.reason:
            response["reason"] = request.reason

        # Include operation context if provided (for frontend to use in retry)
        if request.operationContext:
            response["operationContext"] = request.operationContext

        return response

    except Exception:
        logger.exception("Failed to switch profile")
        return {"success": False, "error": "Failed to switch profile"}


@router.post("/usage-update")
async def request_usage_update():
    """Request a usage update.

    Reads local Claude stats from ~/.claude/stats-cache.json and calculates
    approximate usage percentages based on typical daily limits.

    Note: Returns raw data object (not wrapped in {success, data}) because
    the frontend api-client.ts automatically adds that wrapper.
    """
    from datetime import datetime, timedelta

    stats_file = Path.home() / ".claude" / "stats-cache.json"

    # Default response
    default_response = {
        "sessionPercent": 0,
        "weeklyPercent": 0,
        "sessionResetTime": None,
        "weeklyResetTime": None,
        "profileId": "local",
        "profileName": "Local Stats",
        "fetchedAt": datetime.now().isoformat(),
        "limitType": None
    }

    if not stats_file.exists():
        return default_response

    try:
        stats = json.loads(stats_file.read_text())

        # Get today's date
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # Calculate today's usage
        daily_activity = stats.get("dailyActivity", [])
        today_messages = 0
        weekly_messages = 0

        for day in daily_activity:
            if day.get("date") == today:
                today_messages = day.get("messageCount", 0)
            if day.get("date") >= week_ago:
                weekly_messages += day.get("messageCount", 0)

        # Usage limits (actual confirmed values)
        # Daily limit: 10,000 messages
        # Weekly limit: 10,000 × 7 = 70,000 messages
        # Resets on Thursday weekly
        DAILY_LIMIT_ESTIMATE = 10000
        WEEKLY_LIMIT_ESTIMATE = 70000

        session_percent = min(100, int((today_messages / DAILY_LIMIT_ESTIMATE) * 100))
        weekly_percent = min(100, int((weekly_messages / WEEKLY_LIMIT_ESTIMATE) * 100))

        # Get model usage info
        model_usage = stats.get("modelUsage", {})
        total_output_tokens = sum(m.get("outputTokens", 0) for m in model_usage.values())

        return {
            "sessionPercent": session_percent,
            "weeklyPercent": weekly_percent,
            "sessionResetTime": "Midnight",
            "weeklyResetTime": "Weekly",
            "profileId": "local",
            "profileName": f"Local ({today_messages} msgs today)",
            "fetchedAt": datetime.now().isoformat(),
            "limitType": "session" if session_percent > weekly_percent else "weekly",
            # Extra stats for tooltip
            "todayMessages": today_messages,
            "weeklyMessages": weekly_messages,
            "totalOutputTokens": total_output_tokens
        }

    except (json.JSONDecodeError, KeyError, TypeError):
        return default_response


# --------------------------------------------------------------------------
# Source Environment
# --------------------------------------------------------------------------

class SourceEnvUpdate(BaseModel):
    """Model for updating Magestic AI source environment configuration."""
    claudeToken: str | None = Field(None, description="Claude Code OAuth token (CLAUDE_CODE_OAUTH_TOKEN)")
    anthropicBaseUrl: str | None = Field(None, description="Custom Anthropic API endpoint (ANTHROPIC_BASE_URL)")
    graphitiEnabled: bool | None = Field(None, description="Enable Graphiti memory system (GRAPHITI_ENABLED)")
    githubToken: str | None = Field(None, description="GitHub personal access token (GITHUB_TOKEN)")
    openaiApiKey: str | None = Field(None, description="OpenAI API key for Graphiti (OPENAI_API_KEY)")
    debug: bool | None = Field(None, description="Enable debug mode (DEBUG)")


@router.get("/source-env")
async def get_source_env():
    """Get source environment configuration."""
    return {"success": True, "data": {}}


@router.patch("/source-env")
async def update_source_env(config: SourceEnvUpdate):
    """
    Update Magestic AI source environment configuration.

    Updates the apps/backend/.env file with environment variables for:
    - Claude authentication (CLAUDE_CODE_OAUTH_TOKEN)
    - Custom API endpoints (ANTHROPIC_BASE_URL)
    - Graphiti memory system (GRAPHITI_ENABLED)
    - GitHub integration (GITHUB_TOKEN)
    - OpenAI integration for Graphiti (OPENAI_API_KEY)
    - Debug mode (DEBUG)

    Only updates fields that are provided (partial updates supported).
    Sets secure file permissions (0o600) to protect sensitive tokens.

    Args:
        config: Environment update configuration with optional fields

    Returns:
        Success response with confirmation message

    Raises:
        HTTPException: For validation errors or file system errors
    """
    try:
        settings = get_settings()
        backend_path = Path(settings.BACKEND_PATH)
        env_path = backend_path / ".env"

        # Read existing .env or start fresh
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    existing[key.strip()] = value.strip()

        # Get only provided fields (exclude None values)
        config_dict = config.model_dump(exclude_none=True)

        # Map configuration fields to environment variables
        # Token fields (string values that need validation)
        token_mapping = {
            "claudeToken": "CLAUDE_CODE_OAUTH_TOKEN",
            "githubToken": "GITHUB_TOKEN",
            "openaiApiKey": "OPENAI_API_KEY",
        }

        for config_key, env_key in token_mapping.items():
            if config_key in config_dict:
                value = config_dict[config_key]
                if value:
                    # Strip whitespace and validate token is not empty
                    value = value.strip()
                    if not value:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{config_key} cannot be empty"
                        )
                    # Validate minimum token length for security
                    if len(value) < 10:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{config_key} must be at least 10 characters"
                        )
                    existing[env_key] = value
                else:
                    # Remove token if explicitly set to empty string
                    existing.pop(env_key, None)

        # Map URL fields (string values)
        if "anthropicBaseUrl" in config_dict:
            value = config_dict["anthropicBaseUrl"]
            if value:
                value = value.strip()
                # Validate URL format
                if not value.startswith(("http://", "https://")):
                    raise HTTPException(
                        status_code=400,
                        detail="anthropicBaseUrl must start with http:// or https://"
                    )
                existing["ANTHROPIC_BASE_URL"] = value
            else:
                # Remove if explicitly set to empty string
                existing.pop("ANTHROPIC_BASE_URL", None)

        # Map boolean fields (convert to "true"/"false" strings)
        bool_mapping = {
            "graphitiEnabled": "GRAPHITI_ENABLED",
            "debug": "DEBUG",
        }

        for config_key, env_key in bool_mapping.items():
            if config_key in config_dict:
                value = config_dict[config_key]
                existing[env_key] = "true" if value else "false"

        # Write updated .env file
        env_lines = []

        # Add header comment
        env_lines.append("# Magestic AI Environment Configuration")
        env_lines.append("# Updated via Magestic AI web interface")
        env_lines.append("")

        # Write all environment variables
        for key, value in sorted(existing.items()):
            env_lines.append(f"{key}={value}")

        env_content = "\n".join(env_lines)
        env_path.write_text(env_content)

        # Set secure file permissions (owner read/write only)
        # CRITICAL: .env files often contain sensitive API keys and tokens
        env_path.chmod(0o600)

        return {
            "success": True,
            "message": "Source environment configuration updated successfully",
            "updated_fields": list(config_dict.keys())
        }

    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse existing .env file: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update source environment: {str(e)}"
        )


@router.get("/source-token-check")
async def check_source_token():
    """Check source token status."""
    return {"success": True, "data": {"valid": False}}


# --------------------------------------------------------------------------
# CLI Tools Info
# --------------------------------------------------------------------------

@router.get("/auth-status")
async def get_auth_status():
    """Check if any OAuth token is configured."""
    from ..paths import get_data_file

    profiles_file = get_data_file("claude-profiles.json")
    has_token = False
    profile_count = 0
    email = None

    if profiles_file.exists():
        try:
            data = json.loads(profiles_file.read_text())
            profiles = data.get("profiles", [])
            profile_count = len(profiles)
            active_id = data.get("activeProfileId")
            for p in profiles:
                if p.get("oauthToken"):
                    has_token = True
                    # Get email from active profile, or first profile with a token
                    if email is None or p.get("id") == active_id:
                        email = p.get("email")
        except (json.JSONDecodeError, KeyError):
            pass

    # Also check env var fallback
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")

    # Verify Claude CLI is actually installed before reporting hasToken
    claude_installed = shutil.which("claude") is not None
    if not claude_installed:
        try:
            result = subprocess.run(
                ["bash", "-l", "-c", "which claude"],
                capture_output=True, text=True, timeout=5,
            )
            claude_installed = result.returncode == 0
        except Exception:
            pass

    return {
        "hasToken": (has_token or bool(env_token)) and claude_installed,
        "profileCount": profile_count,
        "source": "profile" if has_token else ("env" if env_token else None),
        "email": email,
    }


@router.get("/claude-credentials-exist")
async def check_claude_credentials_exist():
    """Check if ~/.claude/.credentials.json exists with a valid token."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    exists = False

    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text())
            token = data.get("claudeAiOauth", {}).get("accessToken")
            exists = bool(token and token.startswith("sk-ant-oat01-"))
        except (json.JSONDecodeError, KeyError):
            pass

    return {"exists": exists}


@router.post("/import-claude-credentials")
async def import_claude_credentials():
    """Import token from ~/.claude/.credentials.json into TFactory profiles."""
    from ..paths import get_data_file

    cred_path = Path.home() / ".claude" / ".credentials.json"

    if not cred_path.exists():
        raise HTTPException(status_code=404, detail="Claude credentials file not found")

    try:
        data = json.loads(cred_path.read_text())
        token = data.get("claudeAiOauth", {}).get("accessToken")
        if not token or not token.startswith("sk-ant-oat01-"):
            raise HTTPException(status_code=400, detail="No valid OAuth token found in credentials file")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid credentials file format")

    # Create or update profiles file
    profiles_file = get_data_file("claude-profiles.json")
    profiles_file.parent.mkdir(parents=True, exist_ok=True)

    if profiles_file.exists():
        try:
            profiles_data = json.loads(profiles_file.read_text())
        except json.JSONDecodeError:
            profiles_data = {"profiles": [], "activeProfileId": None}
    else:
        profiles_data = {"profiles": [], "activeProfileId": None}

    # Create an imported profile
    from datetime import datetime
    profile_id = f"imported-{int(time.time())}"
    new_profile = {
        "id": profile_id,
        "name": "Claude Code (Imported)",
        "oauthToken": token,
        "isDefault": len(profiles_data["profiles"]) == 0,
        "createdAt": datetime.now().isoformat(),
    }

    profiles_data["profiles"].append(new_profile)
    profiles_data["activeProfileId"] = profile_id

    write_secret_file(profiles_file, json.dumps(profiles_data, indent=2))

    # Also set env var for immediate use
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token

    return {
        "success": True,
        "profileId": profile_id,
        "profileName": new_profile["name"],
    }
