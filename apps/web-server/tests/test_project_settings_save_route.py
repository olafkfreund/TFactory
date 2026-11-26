"""PATCH /settings accepts the Memory tab payload for project admins and owners."""

import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response
from starlette.responses import Response as StarletteResponse

for root in (
    Path(__file__).resolve().parents[1],
    Path(__file__).resolve().parents[2] / "backend",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from server.database.engine import get_db  # noqa: E402
from server.routes import project_authz, projects  # noqa: E402


@pytest.fixture
def save_projects(monkeypatch: pytest.MonkeyPatch) -> Mock:
    save = Mock()
    monkeypatch.setattr(projects, "save_projects", save)
    return save


@pytest.fixture
def settings_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    membership_role: str | None,
    save_projects: Mock,  # noqa: ARG001 - requested for its patch of projects.save_projects
) -> Response:
    project_data = {"known": {"path": str(tmp_path), "org_id": "A", "name": "p"}}
    monkeypatch.setattr(projects, "load_projects", lambda: project_data)
    monkeypatch.setattr(project_authz, "_load_projects", projects.load_projects)
    monkeypatch.setattr(
        project_authz,
        "get_settings",
        lambda: SimpleNamespace(
            DISABLE_AUTH=False,
            PROJECTS_BACKEND="json",
            LEGACY_SHARED_TOKEN_FULL_ACCESS=False,
        ),
    )
    monkeypatch.setattr(project_authz, "_audit_authz_denial", AsyncMock())

    membership = SimpleNamespace(role=membership_role) if membership_role else None
    db_session = AsyncMock()
    db_session.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: membership
    )

    async def db() -> AsyncIterator[AsyncMock]:
        yield db_session

    app = FastAPI()
    app.dependency_overrides[get_db] = db

    @app.middleware("http")
    async def identity(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        request.state.user = {"id": "u1", "email": "u@example.test", "role": "member"}
        return await call_next(request)

    app.include_router(projects.router, prefix="/api/projects")
    payload = projects.ProjectSettings().model_dump(mode="json")
    payload.update(graphitiMcpEnabled=True, graphitiMcpUrl="http://localhost:3102/mcp/")
    with TestClient(app) as client:
        response: Response = client.patch("/api/projects/known/settings", json=payload)
        return response


@pytest.mark.parametrize(
    ("membership_role", "status", "detail"),
    [
        ("member", 403, "This action requires at least 'admin' project role"),
        ("viewer", 403, "This action requires at least 'admin' project role"),
        (None, 404, "Project not found"),
    ],
)
def test_memory_settings_save_denied(
    settings_response: Response,
    tmp_path: Path,
    save_projects: Mock,
    status: int,
    detail: str,
) -> None:
    assert settings_response.status_code == status
    assert settings_response.json()["detail"] == detail
    assert not (tmp_path / ".tfactory" / ".env").exists()
    save_projects.assert_not_called()


@pytest.mark.parametrize("membership_role", ["admin", "owner"])
def test_memory_settings_save_allowed(
    settings_response: Response, tmp_path: Path, save_projects: Mock
) -> None:
    assert settings_response.status_code == 200
    assert settings_response.json()["success"] is True
    env = (tmp_path / ".tfactory" / ".env").read_text().splitlines()
    assert "GRAPHITI_ENABLED=true" in env
    assert "GRAPHITI_MCP_URL=http://localhost:3102/mcp/" in env
    save_projects.assert_called_once()
