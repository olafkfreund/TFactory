"""/files/discover must refuse base paths outside the browse roots."""

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from server.routes import files, projects


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    (r / "proj" / ".git").mkdir(parents=True)
    return r


@pytest.fixture
def client(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APP_FILE_BROWSE_ROOTS", str(root))
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    monkeypatch.setattr(files, "_is_app_internal_path", lambda _p: False)
    app = FastAPI()
    app.include_router(files.router)
    return TestClient(app)


def test_inside_root_ok(client: TestClient, root: Path) -> None:
    r = client.get("/discover", params={"base_path": str(root)})
    assert r.status_code == 200
    assert "proj" in str(r.json())


def test_outside_roots_403(client: TestClient) -> None:
    assert client.get("/discover", params={"base_path": "/etc"}).status_code == 403


@pytest.mark.usefixtures("client")
@pytest.mark.asyncio
async def test_scan_outside_roots_403(root: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        await projects.scan_for_projects(projects.ScanProjectsRequest(basePath="/etc"))
    assert exc.value.status_code == 403
    await projects.scan_for_projects(projects.ScanProjectsRequest(basePath=str(root)))
