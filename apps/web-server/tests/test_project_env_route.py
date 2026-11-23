"""GET /env returns credentials only to an effective admin or owner."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

for root in (
    Path(__file__).resolve().parents[1],
    Path(__file__).resolve().parents[2] / "backend",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from server.database.engine import get_db  # noqa: E402
from server.routes import context, project_authz, projects  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".tfactory").mkdir()
    (tmp_path / ".tfactory" / ".env").write_text(
        "GIT_TOKEN=git-sentinel\nGIT_BASE_URL=https://user:password@git.example:8443/api\n"
        "GIT_REPO=https://token@git.example/repo\n"
        "OPENAI_API_KEY=openai-sentinel\nVOYAGE_API_KEY=voyage-sentinel\n"
        "GOOGLE_API_KEY=google-sentinel\nAZURE_OPENAI_API_KEY=azure-sentinel\n"
    )
    monkeypatch.setattr(
        projects,
        "load_projects",
        lambda: {"known": {"path": str(tmp_path), "org_id": "A"}},
    )
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

    role: list[str] = ["viewer"]
    db_session = AsyncMock()
    db_session.execute.side_effect = lambda *_: SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(role=role[0])
    )

    async def db():
        yield db_session

    app = FastAPI()
    app.dependency_overrides[get_db] = db

    @app.middleware("http")
    async def identity(request: Request, call_next):
        selected = request.headers.get("X-Role", "viewer")
        role[0] = "owner" if selected == "scoped-owner" else selected
        request.state.user = {
            "id": selected,
            "org_id": "B" if selected == "invisible" else "A",
        }
        if selected == "scoped-owner":
            request.state.user.update(api_key_id="read-key", scopes=["mcp:read"])
        return await call_next(request)

    app.include_router(context.project_router, prefix="/api/projects/{projectId}")
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("role", ["viewer", "member", "scoped-owner"])
def test_redacted_env(client: TestClient, role: str):
    response = client.get("/api/projects/known/env", headers={"X-Role": role})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["graphitiKeysSet"] is True
    assert data["gitBaseUrl"] == "https://git.example:8443/api"
    assert data["gitRepo"] == data["githubRepo"] == "https://git.example/repo"
    assert "gitToken" not in data
    assert "graphitiProviderConfig" not in data
    assert "graphitiMcpEnabled" not in data
    assert "mcpEnabled" not in data
    assert not any(
        secret in response.text
        for secret in (
            "git-sentinel",
            "openai-sentinel",
            "voyage-sentinel",
            "google-sentinel",
            "azure-sentinel",
            "password",
        )
    )


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_privileged_env(client: TestClient, role: str):
    data = client.get("/api/projects/known/env", headers={"X-Role": role}).json()[
        "data"
    ]
    assert data["gitToken"] == "git-sentinel"
    assert data["graphitiProviderConfig"]["openaiApiKey"] == "openai-sentinel"


def test_member_cannot_patch(client: TestClient):
    response = client.patch(
        "/api/projects/known/env", headers={"X-Role": "member"}, json={}
    )
    assert response.status_code == 403


def test_unknown_and_invisible_are_identical(client: TestClient):
    unknown = client.get("/api/projects/unknown/env", headers={"X-Role": "viewer"})
    invisible = client.get("/api/projects/known/env", headers={"X-Role": "invisible"})
    assert unknown.status_code == invisible.status_code == 404
    assert unknown.json() == invisible.json() == {"detail": "Project not found"}


@pytest.mark.parametrize(
    ("base", "repo", "expected"),
    [
        ("user:pass@host/api", "glpat-x@gitlab.com/o/r", "host/api"),
        ("glpat-x@gitlab.com", "https://token@git.example/r", "gitlab.com"),
        ("https://[::1", "token@git.example/r", ""),
    ],
)
def test_redaction_handles_scheme_less_and_invalid_urls(
    client: TestClient, tmp_path: Path, base: str, repo: str, expected: str
) -> None:
    (tmp_path / ".tfactory" / ".env").write_text(
        f"GIT_BASE_URL={base}\nGIT_REPO={repo}\n"
    )
    response = client.get("/api/projects/known/env", headers={"X-Role": "viewer"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["gitBaseUrl"] == expected
    assert (
        data["gitRepo"]
        == data["githubRepo"]
        == repo.replace("token@", "").replace("glpat-x@", "")
    )


def test_resolved_but_missing_json_project_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "load_projects", lambda: {})
    response = client.get("/api/projects/known/env", headers={"X-Role": "owner"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}
