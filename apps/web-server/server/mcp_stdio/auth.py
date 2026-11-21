"""Auth + scope gating for the stdio-MCP proxy (Issue #154).

Routes under ``/api/mcp-stdio/`` accept two kinds of bearer tokens:

1. **acw_ API keys** minted via ``routes/api_keys.py`` — validated
   against the ``ApiKey`` table, hashed at rest, scope-gated. This is
   the recommended path for enterprise installs: each developer holds
   a scoped key, no shared host-wide secret.

2. **Legacy admin token** (``settings.API_TOKEN`` — usually backed by
   ``~/.tfactory/.token``) — acts as a wildcard "all scopes" key.
   This preserves v1.0 behavior so single-user laptops keep working
   without minting a key.

Scopes are the same vocabulary used by ``mcp_remote`` plus a few
finer-grained ones for write operations:

- ``mcp:read``     — read tools (list/status/logs)
- ``project:write`` — create projects
- ``task:write``    — start, stop, recover, approve, create-and-run tasks
- ``task:merge``    — create-pr, merge-pr (default-branch blast radius)

Use ``require_acw_scope(scope)`` as a FastAPI dependency on each
proxy route. It returns the resolved ``AuthenticatedKey`` (or a
synthetic legacy key) so handlers can record key_id in audit logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import _is_legacy_api_token
from server.database.engine import get_db
from server.error_ref import InputRejectedError, client_error
from server.routes.project_authz import require_project_role

from ..mcp_remote import auth as mcp_remote_auth

logger = logging.getLogger(__name__)

# Re-export the named scopes so callers don't have to know which
# module owns the constants.
MCP_READ_SCOPE = "mcp:read"
PROJECT_WRITE_SCOPE = "project:write"
TASK_WRITE_SCOPE = "task:write"
TASK_MERGE_SCOPE = "task:merge"

ALL_SCOPES = frozenset(
    {
        MCP_READ_SCOPE,
        PROJECT_WRITE_SCOPE,
        TASK_WRITE_SCOPE,
        TASK_MERGE_SCOPE,
    }
)


@dataclass(frozen=True)
class _LegacyAdminKey:
    """Synthetic key returned when the caller authenticated via the
    legacy admin token. Behaves like a real ``AuthenticatedKey`` but
    advertises every scope so existing v1.0 callers keep working.

    Mirroring the surface (``key_id``, ``user_id``, ``has_scope``) lets
    handlers treat both auth paths uniformly. ``key_id="legacy-admin"``
    in audit logs makes it easy to spot calls that bypassed scopes.
    """

    key_id: str = "legacy-admin"
    user_id: str | None = None

    @property
    def scopes(self) -> frozenset[str]:
        return ALL_SCOPES

    def has_scope(self, _scope: str) -> bool:
        return True


def acw_principal(key: mcp_remote_auth.AuthenticatedKey | _LegacyAdminKey) -> dict:
    if isinstance(key, _LegacyAdminKey):
        return {"id": "default", "is_legacy_token": True}
    return {
        "id": key.user_id,
        "api_key_id": key.key_id,
        "org_id": key.org_id,
        "scopes": sorted(key.scopes),
        "scoped_service": key.user_id is None,
    }


def require_acw_principal(scope: str):
    scoped = require_acw_scope(scope)

    async def _check(request: Request) -> dict:
        return acw_principal(await scoped(request))

    return _check


def require_acw_access(scope: str, min_role: str = "viewer"):
    scoped = require_acw_scope(scope)

    async def _check(request: Request, db: AsyncSession = Depends(get_db)):  # noqa: B008
        key = await scoped(request)
        task_id = request.path_params.get("task_id")
        project_id = (
            task_id.partition(":")[0]
            if task_id
            else request.query_params.get("project_id")
        )
        await require_project_role(acw_principal(key), project_id, min_role, db)
        return key

    return _check


def _strip_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[7:].strip()


def require_acw_scope(scope: str):
    """FastAPI ``Depends`` factory — returns a coroutine that resolves
    the auth header into either an ``AuthenticatedKey`` (acw_ path) or
    a ``_LegacyAdminKey`` (legacy admin path), and rejects when the
    resolved key lacks ``scope``.

    HTTP status mapping:
    - Missing / malformed Authorization → 401
    - Unknown / disabled acw_ key       → 401
    - Authenticated but lacks scope      → 403

    The 401-vs-403 split matters for the stdio MCP client's error
    messages: 401 says "your key is bad" (regenerate); 403 says "your
    key works but is scoped wrong" (mint a new one with the right
    scope).
    """

    async def _check(request: Request):
        token = _strip_bearer(request.headers.get("Authorization"))
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or malformed Authorization header (expected 'Bearer <token>')",
            )

        # Legacy admin token = wildcard. Matched constant-time via the
        # shared helper, which also refuses to authenticate anything when
        # API_TOKEN is unset (Factory#324 M1).
        if _is_legacy_api_token(token):
            return _LegacyAdminKey()

        try:
            key = await mcp_remote_auth.authenticate(f"Bearer {token}")
        except mcp_remote_auth.MCPAuthError as exc:
            # MCPAuthError's messages are developer-written ("Invalid API
            # key", "API key has expired", etc. -- verified across all 6
            # raise sites in mcp_remote/auth.py, none interpolate an inner
            # exception).
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=client_error(
                    logger, "authentication failed", InputRejectedError(exc.args[0])
                ),
            ) from exc

        if not key.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"API key lacks required scope '{scope}'. "
                    f"Mint a new key with the right scope via Settings → API Keys."
                ),
            )

        return key

    return _check
