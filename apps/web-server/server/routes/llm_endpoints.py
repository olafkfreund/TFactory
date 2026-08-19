"""
LLM Endpoints routes — user-defined OpenAI-compatible LLM endpoints.

Provides CRUD for endpoints (LM Studio, vLLM, OpenRouter, etc.) plus a
``POST /test`` action that probes ``{base_url}/v1/models`` to confirm
reachability and list available models.

Endpoints:
- GET    /api/llm-endpoints           — list current user's endpoints
- POST   /api/llm-endpoints           — create a new endpoint
- GET    /api/llm-endpoints/{id}      — fetch one endpoint
- PUT    /api/llm-endpoints/{id}      — update an endpoint
- DELETE /api/llm-endpoints/{id}      — delete an endpoint
- POST   /api/llm-endpoints/test      — test arbitrary credentials (no save)
- POST   /api/llm-endpoints/{id}/test — test stored credentials
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.error_ref import InputRejectedError, client_error
from server.services.url_safety import (
    assert_safe_outbound_url,
    build_no_redirect_opener,
)

from ..database import LLMEndpoint, User
from ..database.engine import get_db
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm-endpoints", tags=["LLM Endpoints"])

# Mask everything but the last 4 chars when returning an api_key to the UI
_API_KEY_TAIL_LEN = 4


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EndpointCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl
    api_key: SecretStr | None = Field(default=None, max_length=512)
    default_model: str = Field(min_length=1, max_length=255)
    headers: dict[str, str] | None = None


class EndpointUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: HttpUrl | None = None
    api_key: SecretStr | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, min_length=1, max_length=255)
    headers: dict[str, str] | None = None


class EndpointTestRequest(BaseModel):
    """For testing arbitrary credentials before saving."""

    base_url: HttpUrl
    api_key: SecretStr | None = None
    headers: dict[str, str] | None = None


class EndpointResponse(BaseModel):
    id: str
    label: str
    base_url: str
    api_key_preview: str | None
    default_model: str
    headers: dict[str, str] | None
    created_at: str
    updated_at: str


class EndpointTestResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    models: list[str] = []
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_key(key: str | None) -> str | None:
    """Return a masked preview of the API key (last 4 chars shown)."""
    if not key:
        return None
    if len(key) <= _API_KEY_TAIL_LEN:
        return "*" * len(key)
    return "*" * (len(key) - _API_KEY_TAIL_LEN) + key[-_API_KEY_TAIL_LEN:]


def _to_response(endpoint: LLMEndpoint) -> EndpointResponse:
    headers: dict[str, str] | None = None
    if endpoint.headers_json:
        try:
            headers = json.loads(endpoint.headers_json)
        except json.JSONDecodeError:
            headers = None
    return EndpointResponse(
        id=endpoint.id,
        label=endpoint.label,
        base_url=endpoint.base_url,
        api_key_preview=_mask_key(endpoint.api_key),
        default_model=endpoint.default_model,
        headers=headers,
        created_at=endpoint.created_at.isoformat(),
        updated_at=endpoint.updated_at.isoformat(),
    )


def _safe_probe_models_url(base_url: str) -> str:
    """Validate a BYO LLM endpoint's base URL and return the URL to probe.

    PERMISSIVE posture (``allow_private=True``). The whole point of a BYO
    endpoint is that the operator points it at a server they run: LM Studio on
    ``localhost:1234``, vLLM on a cluster-internal address, an OpenAI-compatible
    gateway on the LAN. Refusing RFC-1918 here would delete the feature. What
    the guard keeps is what costs no legitimate use: an http(s) scheme, and a
    hard refusal of the cloud metadata range -- never anyone's LM Studio, and
    the single highest-value SSRF target. Strict-posture cloud API base URLs go
    through routes/settings_api_profiles.py instead.

    This probe had no guard at all before #1047, and it is a live sink in two
    ways: ``POST /api/llm-endpoints/test`` passes a base URL straight out of the
    request body, and ``POST /api/llm-endpoints/{id}/test`` replays one stored
    on the row. Both attach the endpoint's API key as a Bearer header, so an
    unguarded URL hands a credential to whatever it reaches.

    Unlike the local-provider helper this does NOT collapse the URL to
    ``scheme://netloc``: an OpenAI-compatible base legitimately carries a path
    prefix (``https://gateway.example/openai``), and dropping it would break
    every such endpoint. Query and fragment ARE dropped, which is where the
    truncate-the-appended-path trick lives.
    """
    parsed = urllib.parse.urlparse((base_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InputRejectedError("Invalid base URL (only http/https with a host)")
    normalized = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")
    )
    assert_safe_outbound_url(normalized, allow_private=True)
    return f"{normalized}/v1/models"


def _probe_models(
    base_url: str,
    api_key: str | None,
    headers: dict[str, str] | None,
    timeout: int = 10,
) -> EndpointTestResponse:
    """Synchronous probe of ``{base_url}/v1/models``.

    Returns an ``EndpointTestResponse`` describing reachability, status code,
    and available model IDs.  Catches all network errors so the caller gets
    structured feedback instead of an exception.
    """
    try:
        url = _safe_probe_models_url(base_url)
    except ValueError as exc:
        # client_error hands an InputRejectedError message back verbatim -- the
        # operator needs to know their base URL was refused and why. Any OTHER
        # ValueError reaching here comes from a library rather than our
        # validator and gets a correlation id instead of being echoed (#1073).
        return EndpointTestResponse(
            ok=False, error=client_error(logger, "disallowed LLM base URL", exc)
        )

    req_headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        # No-redirect opener: the guard checked the host that was configured,
        # and a 302 to 169.254.169.254 defeats any check made before the
        # request was sent -- with the Bearer header attached.
        with build_no_redirect_opener().open(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return EndpointTestResponse(
            ok=False,
            status_code=exc.code,
            error=f"HTTP {exc.code} {exc.reason}",
        )
    except urllib.error.URLError as exc:
        return EndpointTestResponse(
            ok=False,
            error=f"Connection failed: {exc.reason}",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return EndpointTestResponse(ok=False, error=f"Unexpected error: {exc}")

    # ``data = None`` on a decode failure rather than an early return: the two
    # rejections share a response shape, and merging them keeps this function
    # under the return-count bar now that the SSRF guard has its own exit.
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    # OpenAI shape: {"data": [{"id": "..."}, ...]}
    # Some servers return a bare list.
    items: list[Any]
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        items = data["data"]
    elif isinstance(data, list):
        items = data
    else:
        return EndpointTestResponse(
            ok=False,
            status_code=status_code,
            error=(
                "Response is not JSON"
                if data is None
                else "Unexpected response shape (no 'data' array)"
            ),
        )

    model_ids: list[str] = []
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name")
            if isinstance(mid, str):
                model_ids.append(mid)
        elif isinstance(item, str):
            model_ids.append(item)

    return EndpointTestResponse(ok=True, status_code=status_code, models=model_ids)


async def _get_owned_endpoint(
    endpoint_id: str, user: User, db: AsyncSession
) -> LLMEndpoint:
    result = await db.execute(
        select(LLMEndpoint).where(
            LLMEndpoint.id == endpoint_id, LLMEndpoint.user_id == user.id
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found"
        )
    return endpoint


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[EndpointResponse])
async def list_endpoints(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EndpointResponse]:
    result = await db.execute(
        select(LLMEndpoint)
        .where(LLMEndpoint.user_id == user.id)
        .order_by(LLMEndpoint.created_at.desc())
    )
    endpoints = result.scalars().all()
    return [_to_response(e) for e in endpoints]


@router.post(
    "",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_endpoint(
    body: EndpointCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EndpointResponse:
    endpoint = LLMEndpoint(
        user_id=user.id,
        label=body.label,
        base_url=str(body.base_url),
        api_key=body.api_key.get_secret_value() if body.api_key else None,
        default_model=body.default_model,
        headers_json=json.dumps(body.headers) if body.headers else None,
    )
    db.add(endpoint)
    try:
        await db.commit()
    except Exception as exc:  # likely UniqueConstraint violation
        await db.rollback()
        logger.warning("Failed to create LLM endpoint: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An endpoint with that label already exists",
        ) from exc
    await db.refresh(endpoint)
    return _to_response(endpoint)


@router.get("/{endpoint_id}", response_model=EndpointResponse)
async def get_endpoint(
    endpoint_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EndpointResponse:
    endpoint = await _get_owned_endpoint(endpoint_id, user, db)
    return _to_response(endpoint)


@router.put("/{endpoint_id}", response_model=EndpointResponse)
async def update_endpoint(
    endpoint_id: str,
    body: EndpointUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EndpointResponse:
    endpoint = await _get_owned_endpoint(endpoint_id, user, db)

    if body.label is not None:
        endpoint.label = body.label
    if body.base_url is not None:
        endpoint.base_url = str(body.base_url)
    if body.api_key is not None:
        # Empty string clears the key; non-empty replaces it
        endpoint.api_key = body.api_key.get_secret_value() or None
    if body.default_model is not None:
        endpoint.default_model = body.default_model
    if body.headers is not None:
        endpoint.headers_json = json.dumps(body.headers) if body.headers else None

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Failed to update LLM endpoint: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update conflict (duplicate label?)",
        ) from exc
    await db.refresh(endpoint)
    return _to_response(endpoint)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    endpoint = await _get_owned_endpoint(endpoint_id, user, db)
    await db.delete(endpoint)
    await db.commit()


@router.post("/test", response_model=EndpointTestResponse)
async def test_arbitrary(
    body: EndpointTestRequest,
    user: User = Depends(get_current_user),
) -> EndpointTestResponse:
    """Test arbitrary credentials before saving (used by the 'Test' button)."""
    import asyncio

    return await asyncio.to_thread(
        _probe_models,
        str(body.base_url),
        body.api_key.get_secret_value() if body.api_key else None,
        body.headers,
    )


@router.post("/{endpoint_id}/test", response_model=EndpointTestResponse)
async def test_stored(
    endpoint_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EndpointTestResponse:
    """Test the credentials of a stored endpoint."""
    import asyncio

    endpoint = await _get_owned_endpoint(endpoint_id, user, db)
    headers: dict[str, str] | None = None
    if endpoint.headers_json:
        try:
            headers = json.loads(endpoint.headers_json)
        except json.JSONDecodeError:
            headers = None
    return await asyncio.to_thread(
        _probe_models, endpoint.base_url, endpoint.api_key, headers
    )
