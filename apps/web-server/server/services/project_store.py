"""Project persistence abstraction (WS3 slice 1b).

A small store interface over project metadata so the route layer can swap
between the legacy JSON file and an org-scoped DB backend without changing its
logic. The interface mirrors today's ``load_projects()/save_projects()``
contract: a dict keyed by project id → an arbitrary metadata dict.

Backends:
  - ``JsonProjectStore`` — reads/writes ``projects.json`` (current behaviour,
    the default). Single-tenant; no org scoping.
  - ``DbProjectStore`` — reads/writes ``Project`` rows scoped to one org, so
    project listings are isolated per tenant. ``name``/``path`` live in columns;
    any other keys round-trip through ``settings_json``.

Selected by ``APP_PROJECTS_BACKEND`` (``json`` | ``db``) via ``get_project_store``.

This slice is additive: nothing rewires the routes yet (default stays ``json``),
so the live data path is unchanged. The route cutover + org-scoping enforcement
is the next slice (1c).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database.models import Project

ProjectMap = dict[str, dict]

# Columns the DB store keeps as first-class; everything else round-trips
# through settings_json so the JSON↔DB shapes stay faithful.
_COLUMN_KEYS = ("name", "path")


@runtime_checkable
class ProjectStore(Protocol):
    """Persistence for the project-id → metadata map."""

    async def load_all(self) -> ProjectMap: ...

    async def save_all(self, projects: ProjectMap) -> None: ...


class JsonProjectStore:
    """Legacy ``projects.json`` backend (default; single-tenant)."""

    def __init__(self, projects_file: Path) -> None:
        self._file = projects_file

    async def load_all(self) -> ProjectMap:
        if self._file.exists():
            return json.loads(self._file.read_text())
        return {}

    async def save_all(self, projects: ProjectMap) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(projects, indent=2))


class DbProjectStore:
    """Org-scoped DB backend. All reads/writes are confined to ``org_id``."""

    def __init__(self, session: AsyncSession, org_id: str) -> None:
        self._session = session
        self._org_id = org_id

    async def _rows(self) -> list[Project]:
        result = await self._session.execute(
            select(Project).where(Project.org_id == self._org_id)
        )
        return list(result.scalars().all())

    @staticmethod
    def _row_to_data(row: Project) -> dict:
        data: dict[str, Any] = {"name": row.name, "path": row.path}
        if row.settings_json:
            try:
                extra = json.loads(row.settings_json)
                if isinstance(extra, dict):
                    data.update(extra)
            except json.JSONDecodeError:
                pass
        return data

    @staticmethod
    def _split(data: dict) -> tuple[str, str, str | None]:
        name = data.get("name") or ""
        path = data.get("path") or ""
        extra = {k: v for k, v in data.items() if k not in _COLUMN_KEYS and k != "id"}
        return name, path, (json.dumps(extra) if extra else None)

    async def load_all(self) -> ProjectMap:
        return {row.id: self._row_to_data(row) for row in await self._rows()}

    async def save_all(self, projects: ProjectMap) -> None:
        """Reconcile the org's rows to match ``projects`` (upsert + delete).

        Mirrors the JSON "rewrite the whole map" semantics: ids present in
        ``projects`` are upserted; rows in this org whose id is absent are
        deleted. Scoped strictly to ``org_id``.
        """
        existing = {row.id: row for row in await self._rows()}
        incoming_ids = set(projects.keys())

        for pid, data in projects.items():
            name, path, settings_json = self._split(data)
            row = existing.get(pid)
            if row is None:
                self._session.add(
                    Project(
                        id=pid,
                        org_id=self._org_id,
                        name=name,
                        path=path,
                        settings_json=settings_json,
                    )
                )
            else:
                row.name = name
                row.path = path
                row.settings_json = settings_json

        for pid, row in existing.items():
            if pid not in incoming_ids:
                await self._session.delete(row)

        await self._session.commit()


def get_project_store(
    *,
    session: AsyncSession | None = None,
    org_id: str | None = None,
    projects_file: Path | None = None,
) -> ProjectStore:
    """Return the store backend selected by ``APP_PROJECTS_BACKEND``.

    - ``db``: requires ``session`` + ``org_id`` → ``DbProjectStore``.
    - anything else (default ``json``): ``JsonProjectStore`` at ``projects_file``
      (resolved from settings when not given).
    """
    settings = get_settings()
    backend = (settings.PROJECTS_BACKEND or "json").strip().lower()

    if backend == "db":
        if session is None or org_id is None:
            raise ValueError("db project store requires session + org_id")
        return DbProjectStore(session, org_id)

    if projects_file is None:
        projects_file = Path(settings.PROJECTS_DATA_DIR) / "projects.json"
    return JsonProjectStore(projects_file)
