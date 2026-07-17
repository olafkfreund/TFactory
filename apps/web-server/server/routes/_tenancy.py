"""Tenant resolution for verification specs/runs/verdicts (#683).

Mirrors CFactory's pattern (factory-gitops#13): the ingress/oauth2-proxy
stamps ``X-Tenant-Id`` on every request; resolution honours it only when
``TFACTORY_MULTI_TENANT`` is truthy, so local single-tenant behaviour is
unchanged (everything resolves to ``"default"``).

An explicit payload tenant (AIFactory stamps it on handoff) always wins —
it is deliberate data, not an ambient header. Dependency-free on purpose:
this module is imported by route files that backend unit tests load with a
stubbed ``fastapi``.
"""

from __future__ import annotations

import os

DEFAULT_TENANT = "default"
TENANT_HEADER = "X-Tenant-Id"


def multi_tenant_enabled() -> bool:
    """True when TFACTORY_MULTI_TENANT is set to a truthy value."""
    raw = os.environ.get("TFACTORY_MULTI_TENANT", "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def resolve_tenant(
    header_value: str | None = None, payload_tenant: str | None = None
) -> str:
    """Resolve the effective tenant for a request.

    Precedence: explicit payload tenant > ``X-Tenant-Id`` header (only when
    multi-tenant mode is on) > ``"default"``.
    """
    if isinstance(payload_tenant, str) and payload_tenant.strip():
        return payload_tenant.strip()
    if (
        multi_tenant_enabled()
        and isinstance(header_value, str)
        and header_value.strip()
    ):
        return header_value.strip()
    return DEFAULT_TENANT
