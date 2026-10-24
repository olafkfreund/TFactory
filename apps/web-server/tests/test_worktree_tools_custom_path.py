"""customPath must be refused: it let a request choose the program to execute."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes import worktree_tools


@pytest.fixture
def client(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        worktree_tools.subprocess, "Popen", lambda *a, **k: calls.append(a)
    )
    app = FastAPI()
    app.include_router(worktree_tools.router)
    c = TestClient(app)
    c.popen_calls = calls
    return c


@pytest.mark.parametrize(
    ("url", "key"),
    [("/worktree/open-in-ide", "ide"), ("/worktree/open-in-terminal", "terminal")],
)
def test_custom_path_rejected(client, url, key):
    body = {"worktreePath": "/p", key: "x", "customPath": "/bin/sh"}
    r = client.post(url, json=body)
    assert r.status_code == 400
    assert r.json()["detail"] == worktree_tools.CUSTOM_PATH_DISABLED
    assert client.popen_calls == []


def test_without_custom_path_reject_does_not_fire(client, monkeypatch):
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    r = client.post(
        "/worktree/open-in-ide", json={"worktreePath": "/p", "ide": "vscode"}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Path is not within a known project"


def test_ide_command_comes_from_table():
    assert worktree_tools.get_ide_command("vscode", "/p") == ["code", "/p"]
