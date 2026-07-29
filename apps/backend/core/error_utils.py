"""
Shared Error Utilities
======================

Common error detection and classification functions used across
agent sessions, QA, and other modules.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk.types import Message

logger = logging.getLogger(__name__)


def is_tool_concurrency_error(error: Exception) -> bool:
    """
    Check if an error is a 400 tool concurrency error from Claude API.

    Tool concurrency errors occur when too many tools are used simultaneously
    in a single API request, hitting Claude's concurrent tool use limit.

    Args:
        error: The exception to check

    Returns:
        True if this is a tool concurrency error, False otherwise
    """
    error_str = str(error).lower()
    # Check for 400 status AND tool concurrency keywords
    return "400" in error_str and (
        ("tool" in error_str and "concurrency" in error_str)
        or "too many tools" in error_str
        or "concurrent tool" in error_str
    )


def is_rate_limit_error(error: Exception) -> bool:
    """
    Check if an error is a rate limit error (429 or similar).

    Rate limit errors occur when the API usage quota is exceeded,
    either for session limits or weekly limits.

    Args:
        error: The exception to check

    Returns:
        True if this is a rate limit error, False otherwise
    """
    error_str = str(error).lower()

    # Check for HTTP 429 with word boundaries to avoid false positives
    if re.search(r"\b429\b", error_str):
        return True

    # Check for other rate limit indicators
    return any(
        p in error_str
        for p in [
            "limit reached",
            "rate limit",
            "too many requests",
            "usage limit",
            "quota exceeded",
        ]
    )


def is_authentication_error(error: Exception) -> bool:
    """
    Check if an error is an authentication error (401, token expired, etc.).

    Authentication errors occur when OAuth tokens are invalid, expired,
    or have been revoked (e.g., after token refresh on another process).

    Args:
        error: The exception to check

    Returns:
        True if this is an authentication error, False otherwise
    """
    error_str = str(error).lower()

    # Check for HTTP 401 with word boundaries to avoid false positives
    if re.search(r"\b401\b", error_str):
        return True

    # Check for other authentication indicators
    return any(
        p in error_str
        for p in [
            "authentication failed",
            "authentication error",
            "unauthorized",
            "invalid token",
            "token expired",
            "authentication_error",
            "invalid_token",
            "token_expired",
            "not authenticated",
            "http 401",
            "does not have access to claude",
            "please login again",
            # #854: the message the SDK actually emitted when both fleet
            # credentials were dead — "Failed to authenticate: OAuth session
            # expired and could not be refreshed" — matched none of the above,
            # so a revoked credential was classified as a generic error and
            # retried like a transient one.
            "failed to authenticate",
            "session expired",
        ]
    )


async def safe_receive_messages(
    client,
    *,
    caller: str = "agent",
) -> AsyncIterator[Message]:
    """Iterate over SDK messages with resilience against unexpected errors.

    The SDK's ``receive_response()`` async generator can terminate early if:
    1. An unhandled message type slips past the monkey-patch.
    2. A transient parse error corrupts a single message in the stream.
    3. An unexpected ``StopAsyncIteration`` or runtime error occurs mid-stream.

    This wrapper catches per-message errors, logs them, and continues yielding
    subsequent messages so the agent session can complete its work.

    It also detects rate-limit events (surfaced as ``SystemMessage`` with
    subtype ``unknown_rate_limit_event``) and logs a user-visible warning.

    Args:
        client: A ``ClaudeSDKClient`` instance (must be inside ``async with``).
        caller: Label for log messages (e.g., "session", "agent_runner").

    Yields:
        Parsed ``Message`` objects from the SDK response stream.
    """
    try:
        async for msg in client.receive_response():
            # Detect rate-limit events surfaced by the monkey-patch
            msg_type = type(msg).__name__
            if msg_type == "SystemMessage":
                subtype = getattr(msg, "subtype", "")
                if subtype.startswith("unknown_"):
                    original_type = subtype[len("unknown_") :]
                    if "rate_limit" in original_type:
                        data = getattr(msg, "data", {})
                        retry_after = data.get("retry_after") or data.get(
                            "data", {}
                        ).get("retry_after")
                        retry_info = (
                            f" (retry in {retry_after}s)" if retry_after else ""
                        )
                        logger.warning(f"[{caller}] Rate limit event{retry_info}")
                    else:
                        logger.debug(
                            f"[{caller}] Skipping unknown SDK message type: {original_type}"
                        )
                    continue
            yield msg
    except GeneratorExit:
        return
    except Exception as e:
        # An authentication failure is not a stream glitch to be absorbed: the
        # credential is dead, nothing was produced, and every subsequent call
        # fails identically. Swallowing it here handed the caller a clean
        # end-of-stream, which the planner read as "the agent emitted no plan"
        # — so it retried a full session against a credential that could not
        # work, then recorded `planner_invalid_missing_after_retry`, pointing
        # the reader at plan validity when the truth was a revoked token
        # (#854). Re-raise so the caller classifies and reports the real cause.
        if is_authentication_error(e):
            logger.error(f"[{caller}] authentication failed, not retryable: {e}")
            raise
        # Any other mid-stream failure keeps the original behaviour: log and
        # stop gracefully so callers can process whatever was collected so far.
        logger.error(f"[{caller}] SDK response stream terminated unexpectedly: {e}")
        return
