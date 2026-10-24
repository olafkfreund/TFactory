"""/files/discover must refuse base paths outside the browse roots."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes import files


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "root"
    (root / "proj" / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APP_FILE_BROWSE_ROOTS", str(root))
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    monkeypatch.setattr(files, "_is_app_internal_path", lambda p: False)
    app = FastAPI()
    app.include_router(files.router)
    c = TestClient(app)
    c.root = root
    return c


def test_inside_root_ok(client: Any) -> None:
    r = client.get("/discover", params={"base_path": str(client.root)})
    assert r.status_code == 200
    assert "proj" in str(r.json())


def test_outside_roots_403(client: Any) -> None:
    assert client.get("/discover", params={"base_path": "/etc"}).status_code == 403


@pytest.mark.asyncio
async def test_scan_outside_roots_403(client: Any) -> None:
    from fastapi import HTTPException
    from server.routes import projects

    with pytest.raises(HTTPException) as exc:
        await projects.scan_for_projects(projects.ScanProjectsRequest(basePath="/etc"))
    assert exc.value.status_code == 403
    await projects.scan_for_projects(
        projects.ScanProjectsRequest(basePath=str(client.root))
    )
