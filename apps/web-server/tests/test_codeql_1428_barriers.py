"""Call-site barriers for the two re-opening CodeQL alerts (#1428)."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from server.routes import files, git, github
from server.routes._specpath import safe_component


def _make_repo(path: Path) -> Path:
    path.mkdir()
    for cmd in (
        ["init", "-q"],
        [
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
    ):
        subprocess.run(["git", *cmd], cwd=path, check=True)  # noqa: S603, S607
    return path


@pytest.mark.parametrize(
    ("base", "code"),
    [("-x", 400), ("a;b", 400), ("HEAD~1", 400), ("HEAD", 200)],
)
def test_diff_base_allow_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str, code: int
) -> None:
    repo = _make_repo(tmp_path / "repo")
    monkeypatch.setattr(files, "resolve_path", lambda _id, _p: repo)
    monkeypatch.setattr("server.routes.project_authz._auth_disabled", lambda: True)
    monkeypatch.setattr(
        "server.routes.projects.load_projects", lambda: {"p": {"path": str(repo)}}
    )
    app = FastAPI()
    app.include_router(files.router)
    resp = TestClient(app).get("/p/diff", params={"base": base})
    assert resp.status_code == code


def test_serve_rejects_unregistered_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registered = tmp_path / "registered"
    outside = tmp_path / "outside"
    registered.mkdir()
    outside.mkdir()
    (outside / "note.txt").write_text("private")
    monkeypatch.setattr(files, "_validate_serve_token", lambda *_: True)
    monkeypatch.setattr(
        files, "_resolve_serve_principal", lambda *_: {"is_legacy_token": True}
    )
    monkeypatch.setattr(
        "server.routes.projects.load_projects", lambda: {"p": {"path": str(registered)}}
    )
    app = FastAPI()
    app.include_router(files.router, prefix="/api/files")
    response = TestClient(app).get(
        "/api/files/serve", params={"root": str(outside), "path": "note.txt"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_worktree_name_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path / "repo")
    pf = tmp_path / "projects.json"
    pf.write_text(json.dumps({"projects": [{"id": "p", "path": str(repo)}]}))
    monkeypatch.setattr(
        "server.config.get_settings", lambda: SimpleNamespace(projects_file=pf)
    )
    seen: list[str] = []

    def spy(name: str) -> str:
        seen.append(name)
        return safe_component(name)

    monkeypatch.setattr("server.routes.git.safe_component", spy)

    bad = await git.create_worktree("p", git.CreateWorktreeRequest(name="../x"))
    assert bad["success"] is False

    ok = await git.create_worktree("p", git.CreateWorktreeRequest(name="ok-1"))
    assert ok["success"] is True
    assert (repo / ".tfactory" / "worktrees" / "tasks" / "ok-1").is_dir()
    assert seen == ["ok-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "handler", "payload"),
    [
        (git, git.initialize_git, git.InitGitRequest(path="/unregistered")),
        (
            github,
            github.add_git_remote,
            github.AddRemoteRequest(projectPath="/unregistered", repoFullName="o/r"),
        ),
    ],
)
async def test_new_git_path_checks_creator_before_existing_directory(
    monkeypatch, module, handler, payload
):
    calls = []

    def canonical(_path, *, allow_unregistered=False):
        calls.append(allow_unregistered)
        if allow_unregistered:
            pytest.fail("Unregistered path probed before creator authorization")
        raise HTTPException(404, "Project not found")

    async def deny_creator(*_args):
        raise HTTPException(403, "No project creation access")

    monkeypatch.setattr(module, "canonical_project_path", canonical)
    monkeypatch.setattr(module, "project_id_for_path", lambda _path: None)
    monkeypatch.setattr(module, "creator_org", deny_creator)
    request = SimpleNamespace(state=SimpleNamespace(user={"id": "u"}))
    with pytest.raises(HTTPException) as exc:
        await handler(payload, http_request=request, db=object())
    assert exc.value.status_code == 403
    assert calls == [False]
