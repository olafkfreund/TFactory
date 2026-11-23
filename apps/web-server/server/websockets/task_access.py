"""Project access for task-scoped WebSocket streams."""

import time
from typing import Any

from fastapi import WebSocket

from server.auth import (
    WebSocketAuthError,
    _ws_acw_principal,
    _ws_extract_token,
    authenticate_websocket,
)
from server.database.engine import async_session_factory
from server.routes.project_authz import require_project_role

_KEY_RECHECK_SECONDS = 300


async def _authorize_project(principal: dict[str, Any] | None, project_id: str) -> None:
    async with async_session_factory() as db:
        await require_project_role(principal, project_id, "viewer", db)


class TaskSocketAccess:
    def __init__(self, websocket: WebSocket, task_id: str):
        self.websocket = websocket
        self.project_id, separator, spec_id = task_id.partition(":")
        self.valid_task_id = bool(separator and self.project_id and spec_id)
        self.principal: dict[str, Any] | None = None
        self.key_token: str | None = None
        self.last_key_check = time.monotonic()

    async def _authorize(
        self, principal: dict[str, Any] | None, project_id: str | None = None
    ) -> None:
        await _authorize_project(principal, project_id or self.project_id)

    async def connect(self) -> bool:
        try:
            self.principal = await authenticate_websocket(self.websocket)
        except WebSocketAuthError:
            return False
        if not self.valid_task_id:
            await self.websocket.close(code=4003)
            return False

        try:
            await self._authorize(self.principal)
        except Exception:  # noqa: BLE001 - denied or unavailable authorization
            await self.websocket.close(code=4003)
            return False

        token = _ws_extract_token(self.websocket)
        if (
            token
            and token.startswith("acw_")
            and (self.principal or {}).get("api_key_id")
        ):
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


class AllTaskSocketAccess:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.user: dict[str, Any] | None = None
        self.key_token: str | None = None
        self.last_check = time.monotonic()

    async def connect(self) -> bool:
        try:
            self.user = await authenticate_websocket(self.websocket)
        except WebSocketAuthError:
            return False
        except Exception:  # noqa: BLE001 - unexpected auth failures close the socket
            await self.websocket.close(code=4003)
            return False
        if self.user is not None:
            token = _ws_extract_token(self.websocket)
            if token and token.startswith("acw_") and self.user.get("api_key_id"):
                self.key_token = token
                self.last_check = time.monotonic()
        return True

    async def recheck(self) -> bool:
        if (
            not self.key_token
            or time.monotonic() - self.last_check < _KEY_RECHECK_SECONDS
        ):
            return True
        try:
            user = await _ws_acw_principal(self.key_token)
            if user is None:
                raise ValueError("Key revoked")
        except Exception:  # noqa: BLE001 - revoked keys close the socket
            await self.websocket.close(code=4001)
            return False
        self.user = user
        self.last_check = time.monotonic()
        return True

    async def readable(self, task_ids: list[str]) -> set[str]:
        allowed: set[str] = set()
        projects: dict[str, bool] = {}
        for task_id in task_ids:
            project_id, separator, spec_id = task_id.partition(":")
            if not separator or not project_id or not spec_id:
                continue
            if project_id not in projects:
                try:
                    await _authorize_project(self.user, project_id)
                    projects[project_id] = True
                except Exception:  # noqa: BLE001 - strict sockets fail closed on DB failures
                    projects[project_id] = False
            if projects[project_id]:
                allowed.add(task_id)
        return allowed
