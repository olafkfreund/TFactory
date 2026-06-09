"""Resolve the org a request should be scoped to (WS3 slice 1c foundation).

The org-scoped ``DbProjectStore`` (slice 1b) needs an ``org_id`` for every
request. This module turns the authenticated user (``request.state.user``,
populated by ``TokenAuthMiddleware``) into that org id, so the eventual route
cutover can scope reads/writes per tenant.

Resolution precedence for a user:
  1. the user's owned ``Personal`` org (the one created at registration),
  2. any other org they own,
  3. the earliest org they're a member of,
  4. ``None`` if the user belongs to no org.

Pure read helpers — no mutation, no route wiring here.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import OrgMember, Organization


async def resolve_user_org(session: AsyncSession, user_id: str) -> str | None:
    """Return the org id a user's work should be scoped to, or None."""
    owned = (
        await session.execute(
            select(Organization).where(Organization.owner_id == user_id)
        )
    ).scalars().all()
    for org in owned:
        if org.name == "Personal":
            return org.id
    if owned:
        return owned[0].id

    membership = (
        await session.execute(
            select(OrgMember)
            .where(OrgMember.user_id == user_id)
            .order_by(OrgMember.joined_at)
        )
    ).scalars().first()
    return membership.org_id if membership else None


async def resolve_request_org(request: Any, session: AsyncSession) -> str | None:
    """Resolve the org id for the current request from ``request.state.user``.

    Returns None when there's no authenticated user id (e.g. the legacy
    token's ``{"id": "default"}`` with no org) — callers decide how to handle
    an unscoped request.
    """
    user = getattr(getattr(request, "state", None), "user", None)
    user_id = user.get("id") if isinstance(user, dict) else None
    if not user_id:
        return None
    return await resolve_user_org(session, user_id)
