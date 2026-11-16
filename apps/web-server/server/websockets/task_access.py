"""Project access for task-scoped WebSocket streams."""

import logging
import time
from typing import Any

from factory_common.logsafe import sanitize_log
from fastapi import WebSocket

from server.auth import (
    WebSocketAuthError,
    _ws_acw_principal,
    _ws_extract_token,
    authenticate_websocket,
)
from server.config import get_settings
from server.database.engine import async_session_factory
from server.routes.project_authz import authorize_project_for_user

logger = logging.getLogger(__name__)
_KEY_RECHECK_SECONDS = 300


class TaskSocketAccess:
    def __init__(self, websocket: WebSocket, task_id: str):
        self.websocket = websocket
        self.project_id, separator, spec_id = task_id.partition(":")
        self.valid_task_id = bool(separator and self.project_id and spec_id)
        self.principal: dict[str, Any] | None = None
        self.key_token: str | None = None
        self.last_key_check = time.monotonic()

    async def _authorize(self, principal: dict[str, Any]) -> None:
        async with async_session_factory() as db:
            await authorize_project_for_user(principal, self.project_id, db)

    async def connect(self) -> bool:
        try:
            self.principal = await authenticate_websocket(self.websocket)
        except WebSocketAuthError:
            return False
        if self.principal is None:
            return True
        if not self.valid_task_id:
            await self.websocket.close(code=4003)
            return False

        strict = bool(
            self.principal.get("api_key_id") or get_settings().RBAC_ENFORCE_TASK_ROUTES
        )
        try:
            await self._authorize(self.principal)
        except Exception:  # noqa: BLE001 - strict sockets fail closed on DB failures
            if strict:
                await self.websocket.close(code=4003)
                return False
            logger.warning(
                "Task WS access would deny: project=%s user=%s",
                sanitize_log(self.project_id),
                sanitize_log(self.principal.get("id")),
            )

        token = _ws_extract_token(self.websocket)
        if token and token.startswith("acw_") and self.principal.get("api_key_id"):
            self.key_token = token
            self.last_key_check = time.monotonic()
        return True

    async def recheck(self) -> bool:
        if (
            not self.key_token
            or time.monotonic() - self.last_key_check < _KEY_RECHECK_SECONDS
        ):
            return True
        try:
            principal = await _ws_acw_principal(self.key_token)
            if principal is None:
                raise ValueError("Key revoked")
            await self._authorize(principal)
        except Exception:  # noqa: BLE001 - revoked keys and DB failures close the socket
            await self.websocket.close(code=4001)
            return False
        self.principal = principal
        self.last_key_check = time.monotonic()
        return True
