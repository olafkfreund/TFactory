"""Regression tests for JIT provisioning of the recorded org owner (#1702)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from server.database.models import Base, Organization, OrgMember
from server.oidc import provisioning
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    monkeypatch.setenv("APP_OIDC_DEFAULT_ORG_SLUG", "oidc-owner-pin")
    monkeypatch.setenv("APP_OIDC_DEFAULT_ORG_NAME", "OIDC Owner Pin")
    monkeypatch.setenv("APP_OIDC_DEFAULT_ROLE", "member")
    monkeypatch.setenv(
        "APP_OIDC_GROUP_TO_ROLE",
        '{"factory-admins":"owner","factory-members":"member"}',
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recorded_owner_keeps_owner_membership(session: AsyncSession) -> None:
    claims = {
        "sub": "recorded-owner",
        "email": "owner@example.test",
        "groups": ["factory-members"],
    }
    user = await provisioning.jit_provision_user(session, claims)
    org = (await session.scalars(select(Organization))).one()
    membership = (await session.scalars(select(OrgMember))).one()
    assert org.owner_id == membership.user_id == user.id
    assert membership.org_id == org.id
    assert user.role == "member"
    assert membership.role == "owner"

    await provisioning.jit_provision_user(session, claims)
    await session.refresh(membership)
    assert membership.role == "owner"

    membership.role = "member"
    await session.commit()
    await provisioning.jit_provision_user(session, claims)
    await session.refresh(membership)
    assert membership.role == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group", "created_role", "previous_role"),
    [("factory-members", "member", "admin"), ("factory-admins", "owner", "owner")],
)
async def test_non_owner_membership_follows_claim(
    session: AsyncSession, group: str, created_role: str, previous_role: str
) -> None:
    await provisioning.jit_provision_user(
        session, {"sub": "recorded-owner", "email": "owner@example.test"}
    )
    claims = {"sub": "second-user", "email": "second@example.test", "groups": [group]}
    user = await provisioning.jit_provision_user(session, claims)
    org = (await session.scalars(select(Organization))).one()
    result = await session.scalars(
        select(OrgMember).where(
            OrgMember.org_id == org.id, OrgMember.user_id == user.id
        )
    )
    membership = result.one()
    assert org.owner_id != user.id
    assert membership.role == created_role

    membership.role = previous_role
    await session.commit()
    await provisioning.jit_provision_user(
        session, {**claims, "groups": ["factory-members"]}
    )
    await session.refresh(membership)
    assert membership.role == "member"
