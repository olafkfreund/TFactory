"""
Global Events WebSocket with per-client routing.

Supports both broadcast (legacy) and targeted delivery based on
user identity.  When a JWT-authenticated user connects, events
can be routed only to members of the relevant organization.
Legacy (bearer-token) connections receive all events (backward
compatible).
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from factory_common.logsafe import sanitize_log
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.auth import (
    WebSocketAuthError,
    _ws_acw_principal,
    _ws_extract_token,
    authenticate_websocket,
)
from server.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


def load_projects() -> dict[str, dict[str, Any]]:
    """Load the project registry (lazy import to avoid circular dependencies)."""
    from server.routes.projects import load_projects as _load_projects  # noqa: PLC0415

    return _load_projects()


# ---------------------------------------------------------------------------
# Client tracking
# ---------------------------------------------------------------------------


@dataclass
class ConnectedClient:
    """A connected WebSocket client with optional identity."""

    websocket: WebSocket
    user_id: str | None = None
    org_ids: set[str] = field(default_factory=set)
    key_org_id: str | None = None


# Active WebSocket connections — keyed by WebSocket object for fast lookup
_clients: dict[WebSocket, ConnectedClient] = {}

# Legacy set kept for backward compatibility with code that still
# references ``active_connections`` directly.
active_connections: set[WebSocket] = set()
SERVICE_ONLY_ORG = ""
_ORG_CACHE_SECONDS = 30.0
_KEY_RECHECK_SECONDS = 300.0
_org_cache: dict[str, tuple[float, str | None]] = {}


def _register_client(ws: WebSocket, user_info: dict | None) -> ConnectedClient:
    """Register a new client connection."""
    client = ConnectedClient(
        websocket=ws,
        user_id=user_info["id"] if user_info else None,
        key_org_id=(user_info or {}).get("org_id"),
    )
    _clients[ws] = client
    active_connections.add(ws)
    return client


def _unregister_client(ws: WebSocket) -> None:
    """Remove a client connection."""
    _clients.pop(ws, None)
    active_connections.discard(ws)


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------


def _event_org(payload: dict[str, Any]) -> str | None:
    try:
        pid = payload.get("projectId")
        if not pid:
            task_id = payload.get("taskId")
            pid = (
                task_id.split(":", 1)[0]
                if isinstance(task_id, str) and ":" in task_id
                else None
            )
        if not isinstance(pid, str) or not pid:
            return None
        cached = _org_cache.get(pid)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _ORG_CACHE_SECONDS:
            return cached[1]
        org = load_projects().get(pid, {}).get("org_id") or None
        _org_cache[pid] = (now, org)
        return org
    except Exception:  # noqa: BLE001 - unreadable registry must fail closed
        return None


def _payload_org(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    return payload.get("org_id") or (
        data.get("org_id") if isinstance(data, dict) else None
    )


def _jwt_strict() -> bool:
    return bool(get_settings().RBAC_ENFORCE_TASK_ROUTES)


def _may_receive(
    client: ConnectedClient, org: str | None, shadow: bool = False
) -> bool:
    """``shadow`` marks a former broadcast: JWT sockets keep it until REST enforces.

    Explicit ``send_to_org`` sends are members-only in every mode.
    """
    if client.user_id is None:
        return True
    if client.key_org_id is not None:
        # Also needs live membership: a failed lookup or a removed owner gets nothing.
        return org is not None and org == client.key_org_id and org in client.org_ids
    strict_ok = org is not None and org in client.org_ids
    if not shadow or _jwt_strict():
        return strict_ok
    if not strict_ok:
        logger.debug(
            "ws would drop %s for user %s",
            sanitize_log(org),
            sanitize_log(client.user_id),
        )
    return True


async def broadcast_event(event_type: str, payload: dict[str, Any]) -> None:
    """Broadcast an event to all connected clients (legacy behavior)."""
    await _deliver_local_to_org(
        _event_org(payload) or SERVICE_ONLY_ORG, event_type, payload, shadow=True
    )


async def send_to_user(user_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Send an event to a specific user (all their connections)."""
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []

    for ws, client in list(_clients.items()):
        if client.user_id == user_id and (
            client.key_org_id is None or _payload_org(payload) == client.key_org_id
        ):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

    for ws in disconnected:
        _unregister_client(ws)


async def _deliver_local_to_org(
    org_id: str, event_type: str, payload: dict[str, Any], shadow: bool = False
) -> None:
    """Send an event to members of an org connected to this server."""
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []

    for ws, client in list(_clients.items()):
        if _may_receive(client, org_id or None, shadow):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

    for ws in disconnected:
        _unregister_client(ws)


async def send_to_org(org_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Send an event only to members of a specific organization."""
    await _deliver_local_to_org(org_id, event_type, payload)


async def emit_org_less(
    principal: object, event_type: str, payload: dict[str, Any]
) -> None:
    """Send an event that has no org to the human who started it, else to service clients."""
    if (
        isinstance(principal, dict)
        and principal.get("id")
        and not principal.get("is_service")
    ):
        await send_to_user(str(principal["id"]), event_type, payload)
    else:
        await send_to_org(SERVICE_ONLY_ORG, event_type, payload)


def update_client_orgs(user_id: str, org_ids: set[str]) -> None:
    """Update the org memberships for all connections of a given user."""
    for client in _clients.values():
        if client.user_id == user_id:
            client.org_ids = org_ids


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/events")
async def events_websocket(websocket: WebSocket):  # noqa: PLR0912 - socket lifecycle
    """WebSocket endpoint for global events."""
    await websocket.accept()

    # Authenticate — get user info if JWT, None for legacy token
    try:
        user_info = await authenticate_websocket(websocket)
    except WebSocketAuthError:
        return

    client = _register_client(websocket, user_info)
    key_token = _ws_extract_token(websocket) if client.key_org_id else None
    if not key_token or not key_token.startswith("acw_"):
        key_token = None
    last_key_check = time.monotonic()

    # If authenticated user, load their org memberships for routing
    if user_info and user_info.get("id"):
        try:
            from sqlalchemy import select

            from ..database import OrgMember
            from ..database.engine import async_session_factory

            async with async_session_factory() as session:
                result = await session.execute(
                    select(OrgMember.org_id).where(OrgMember.user_id == user_info["id"])
                )
                client.org_ids = {row[0] for row in result.all()}
                if user_info.get("org_id"):
                    client.org_ids &= {user_info["org_id"]}
        except Exception:
            logger.debug("Could not load org memberships for WS client", exc_info=True)

    try:
        # Keep connection alive and listen for pings
        while True:
            try:
                if (
                    key_token
                    and time.monotonic() - last_key_check >= _KEY_RECHECK_SECONDS
                ):
                    if await _ws_acw_principal(key_token) is None:
                        await websocket.close(code=4001)
                        break
                    last_key_check = time.monotonic()

                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)

                # Handle ping/pong
                if data == "ping":
                    await websocket.send_text("pong")

            except TimeoutError:
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _unregister_client(websocket)


# Helper functions for different event types
async def emit_task_progress(task_id: str, progress: dict):
    import logging

    logging.getLogger(__name__).info(
        f"[WebSocket] Emitting task:progress - taskId: {task_id}, percentage: {progress.get('percentage', 'N/A')}%"
    )
    await broadcast_event("task:progress", {"taskId": task_id, **progress})


async def emit_task_status(task_id: str, status: str, review_reason: str | None = None):
    import logging

    payload = {"taskId": task_id, "status": status}
    if review_reason:
        payload["reviewReason"] = review_reason
        logging.getLogger(__name__).info(
            "[WebSocket] Emitting task:status - taskId: %s, status: %s, reviewReason: %s",
            sanitize_log(task_id),
            sanitize_log(status),
            sanitize_log(review_reason),
        )
    else:
        logging.getLogger(__name__).info(
            "[WebSocket] Emitting task:status - taskId: %s, status: %s",
            sanitize_log(task_id),
            sanitize_log(status),
        )
    await broadcast_event("task:status", payload)


async def emit_task_log(task_id: str, log: str):
    import logging

    # Only log the first 50 chars to avoid flooding logs with full log content
    log_preview = (
        log[:50].replace("\n", "\\n") if len(log) > 50 else log.replace("\n", "\\n")
    )
    logging.getLogger(__name__).debug(
        "[WebSocket] Emitting task:log - taskId: %s, log: %s...",
        sanitize_log(task_id),
        sanitize_log(log_preview),
    )
    await broadcast_event("task:log", {"taskId": task_id, "log": log})


async def emit_task_update(task_id: str, task_data: dict):
    """Emit task data update for frontend to refresh task card."""
    import logging

    exec_progress = task_data.get("executionProgress", {})
    phase = exec_progress.get("phase", "N/A") if exec_progress else "N/A"
    progress = exec_progress.get("phaseProgress", "N/A") if exec_progress else "N/A"
    logging.getLogger(__name__).info(
        "[WebSocket] Emitting task:update - taskId: %s, phase: %s, progress: %s%%",
        sanitize_log(task_id),
        sanitize_log(phase),
        sanitize_log(progress),
    )
    await broadcast_event("task:update", {"taskId": task_id, **task_data})


async def emit_insights_chunk(project_id: str, chunk: str):
    await broadcast_event("insights:chunk", {"projectId": project_id, "chunk": chunk})


async def emit_profile_switch(task_id: str, switch_data: dict):
    """Emit profile switch event for reactive failover."""
    import logging

    from_profile = switch_data.get("fromProfile", "N/A")
    to_profile = switch_data.get("toProfile", "N/A")
    logging.getLogger(__name__).info(
        f"[WebSocket] Emitting task:profile-switch - taskId: {task_id}, from: {from_profile}, to: {to_profile}"
    )
    await broadcast_event("task:profile-switch", {"taskId": task_id, **switch_data})


async def emit_task_logs_stream(
    spec_id: str, chunk: dict, project_id: str | None = None
):
    """Emit a task log chunk for real-time streaming to open task detail modals.

    This event streams individual log entries as they're added to task_logs.json,
    enabling live updates in the frontend without file polling.

    Args:
        spec_id: The spec/task identifier (e.g., "007-task-update-progress-logs")
        chunk: The log chunk dict matching TaskLogStreamChunk interface:
            - type: 'text' | 'tool_start' | 'tool_end' | 'phase_start' | 'phase_end' | 'error'
            - content: (optional) Log message content
            - phase: (optional) Current phase (planning, coding, validation)
            - timestamp: (optional) ISO timestamp
            - tool: (optional) { name: string, input?: string, success?: boolean }
            - subtask_id: (optional) Current subtask identifier
    """
    import logging

    chunk_type = chunk.get("type", "unknown")
    content_preview = (
        chunk.get("content", "")[:50].replace("\n", "\\n")
        if chunk.get("content")
        else ""
    )
    logging.getLogger(__name__).debug(
        f"[WebSocket] Emitting task-logs:stream - specId: {sanitize_log(spec_id)}, "
        f"type: {sanitize_log(chunk_type)}, content: {sanitize_log(content_preview)}..."
    )
    payload: dict[str, Any] = {"specId": spec_id, "chunk": chunk}
    if project_id:
        payload["projectId"] = project_id
    await broadcast_event("task-logs:stream", payload)


async def emit_subtask_update(
    task_id: str, subtask_id: str, status: str, previous_status: str | None = None
):
    """Emit a subtask status change event for granular real-time updates.

    This event is emitted when an individual subtask's status changes, allowing
    the frontend to update subtask checkmarks in real-time without waiting for
    the full task update cycle.

    Args:
        task_id: The task/spec identifier
        subtask_id: The subtask identifier (e.g., "1.1", "2.3")
        status: The new status ("pending", "in_progress", "completed", "failed")
        previous_status: The previous status (optional, for logging/debugging)
    """
    import logging

    logger = logging.getLogger(__name__)
    if previous_status:
        logger.info(
            f"[WebSocket] Emitting task:subtask-update - taskId: {sanitize_log(task_id)}, "
            f"subtaskId: {sanitize_log(subtask_id)}, status: {sanitize_log(previous_status)} -> {sanitize_log(status)}"
        )
    else:
        logger.info(
            f"[WebSocket] Emitting task:subtask-update - taskId: {sanitize_log(task_id)}, "
            f"subtaskId: {sanitize_log(subtask_id)}, status: {sanitize_log(status)}"
        )
    await broadcast_event(
        "task:subtask-update",
        {
            "taskId": task_id,
            "subtaskId": subtask_id,
            "status": status,
            "previousStatus": previous_status,
        },
    )
