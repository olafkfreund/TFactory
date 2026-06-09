"""Migrate legacy JSON-stored projects into the DB (WS3 slice 1a).

The legacy ``projects.json`` predates multi-tenancy and has no org/owner field.
Per the WS3 decision (option **a**), each legacy project migrates into its
**owner's Personal org** — resolved as the install's primary user's ``Personal``
organization. On a single-user (self-hosted) install this is unambiguous; if
multiple candidate users exist the migration **fails loudly** rather than guess
(pass ``owner_user_id`` explicitly to disambiguate).

Safety: this only *adds* DB rows. The live ``routes/projects.py`` keeps reading
``projects.json`` — nothing is cut over here, so the migration is fully
reversible (delete the inserted rows). Idempotent: matches existing rows on
``(org_id, path)`` and skips them, so re-running is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import Organization, Project, User


class ProjectMigrationError(RuntimeError):
    """Raised when the migration cannot resolve a safe target (owner / org)."""


@dataclass
class MigrationResult:
    owner_user_id: str
    org_id: str
    created: list[str] = field(default_factory=list)   # paths inserted
    skipped: list[str] = field(default_factory=list)    # paths already present

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


async def _resolve_owner(session: AsyncSession, owner_user_id: str | None) -> str:
    """Resolve the target user id, failing loudly on ambiguity."""
    if owner_user_id:
        user = (
            await session.execute(select(User).where(User.id == owner_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ProjectMigrationError(f"owner_user_id {owner_user_id!r} not found")
        return owner_user_id

    users = (await session.execute(select(User))).scalars().all()
    if len(users) == 1:
        return users[0].id
    if not users:
        raise ProjectMigrationError("no users in the database — cannot resolve an owner")
    raise ProjectMigrationError(
        f"{len(users)} users found — pass owner_user_id explicitly to choose the target"
    )


async def _resolve_personal_org(session: AsyncSession, owner_user_id: str) -> str:
    """Return the owner's Personal org id (prefer name=='Personal')."""
    owned = (
        await session.execute(
            select(Organization).where(Organization.owner_id == owner_user_id)
        )
    ).scalars().all()
    if not owned:
        raise ProjectMigrationError(
            f"user {owner_user_id!r} owns no organization — cannot place projects"
        )
    for org in owned:
        if org.name == "Personal":
            return org.id
    return owned[0].id


def _iter_entries(projects_json: Any) -> Iterator[tuple[str | None, dict]]:
    """Yield (project_id, entry) tolerating both the ``{id: {...}}`` map and the
    ``{"projects": [{...}]}`` list shapes."""
    if isinstance(projects_json, dict) and isinstance(projects_json.get("projects"), list):
        for entry in projects_json["projects"]:
            if isinstance(entry, dict):
                yield entry.get("id"), entry
    elif isinstance(projects_json, dict):
        for pid, entry in projects_json.items():
            if isinstance(entry, dict):
                yield pid, entry


async def migrate_projects_to_db(
    session: AsyncSession,
    projects_json: Any,
    *,
    owner_user_id: str | None = None,
) -> MigrationResult:
    """Insert legacy projects as DB rows in the owner's Personal org.

    Idempotent (skips rows whose ``path`` already exists in the target org).
    Commits once at the end. Raises ``ProjectMigrationError`` if the owner or
    Personal org can't be resolved safely.
    """
    owner = await _resolve_owner(session, owner_user_id)
    org_id = await _resolve_personal_org(session, owner)

    existing_paths = {
        p.path
        for p in (
            await session.execute(select(Project).where(Project.org_id == org_id))
        ).scalars().all()
    }

    result = MigrationResult(owner_user_id=owner, org_id=org_id)
    for _pid, entry in _iter_entries(projects_json):
        path = entry.get("path")
        if not path:
            continue  # malformed legacy entry — skip rather than insert junk
        if path in existing_paths:
            result.skipped.append(path)
            continue
        name = entry.get("name") or Path(path).name
        session.add(
            Project(org_id=org_id, name=name, path=path, created_by=owner)
        )
        existing_paths.add(path)
        result.created.append(path)

    await session.commit()
    return result


# ─── Operator CLI ─────────────────────────────────────────────────────────


async def _run_cli(owner_user_id: str | None) -> MigrationResult:  # pragma: no cover
    import json
    import os

    from ..config import get_settings
    from ..database.engine import async_session_factory

    settings = get_settings()
    projects_file = Path(settings.PROJECTS_DATA_DIR) / "projects.json"
    if not projects_file.exists():
        raise ProjectMigrationError(f"no projects.json at {projects_file}")
    data = json.loads(projects_file.read_text())

    async with async_session_factory() as session:
        return await migrate_projects_to_db(session, data, owner_user_id=owner_user_id)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Migrate legacy projects.json into the DB (owner's Personal org)."
    )
    parser.add_argument(
        "--owner", default=None, help="Owner user id (required if >1 user exists)"
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run_cli(args.owner))
    except ProjectMigrationError as exc:
        print(f"migration aborted: {exc}")
        return 1
    print(
        f"migrated into org {result.org_id} (owner {result.owner_user_id}): "
        f"{result.created_count} created, {result.skipped_count} already present"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
