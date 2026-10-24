"""customPath must be refused: it let a request choose the program to execute."""

import subprocess
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes import worktree_tools


@pytest.fixture
def popen_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **_k: calls.append(a))
    return calls


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(worktree_tools.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("url", "key"),
    [("/worktree/open-in-ide", "ide"), ("/worktree/open-in-terminal", "terminal")],
)
def test_custom_path_rejected(
    client: TestClient, popen_calls: list[Any], url: str, key: str
) -> None:
    body = {"worktreePath": "/p", key: "x", "customPath": "/bin/sh"}
    r = client.post(url, json=body)
    assert r.status_code == 400
    assert r.json()["detail"] == worktree_tools.CUSTOM_PATH_DISABLED
    assert popen_calls == []


def test_without_custom_path_reject_does_not_fire(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    r = client.post(
        "/worktree/open-in-ide", json={"worktreePath": "/p", "ide": "vscode"}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Path is not within a known project"


def test_ide_command_comes_from_table() -> None:
    assert worktree_tools.get_ide_command("vscode", "/p") == ["code", "/p"]
