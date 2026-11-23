"""GET /env returns credentials only to an effective admin or owner."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
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
        "OPENAI_API_KEY=openai-sentinel\nVOYAGE_API_KEY=voyage-sentinel\n"
        "GOOGLE_API_KEY=google-sentinel\nAZURE_OPENAI_API_KEY=azure-sentinel\n"
    )
    monkeypatch.setattr(
        projects, "load_projects", lambda: {"known": {"path": str(tmp_path)}}
    )

    async def authorize(principal, project_id, min_role="viewer", _db=None):
        if project_id != "known" or principal["role"] == "invisible":
            raise HTTPException(404, "Project not found")
        role = "viewer" if principal["role"] == "scoped-owner" else principal["role"]
        levels = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
        if levels[role] < levels[min_role]:
            raise HTTPException(403, "Forbidden")
        return SimpleNamespace(role=role)

    monkeypatch.setattr(project_authz, "require_project_role", authorize)
    monkeypatch.setattr(context, "require_project_role", authorize)

    async def db():
        yield object()

    app = FastAPI()
    app.dependency_overrides[get_db] = db

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user = {"role": request.headers.get("X-Role", "viewer")}
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
    assert "gitToken" not in data
    assert "graphitiProviderConfig" not in data
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
