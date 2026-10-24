"""Call-site barriers for the two re-opening CodeQL alerts (#1428)."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes import files, git


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
        subprocess.run(["git", *cmd], cwd=path, check=True)
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
    app = FastAPI()
    app.include_router(files.router)
    resp = TestClient(app).get("/p/diff", params={"base": base})
    assert resp.status_code == code


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
    real = git.safe_component

    def spy(name: str) -> str:
        seen.append(name)
        return real(name)

    monkeypatch.setattr(git, "safe_component", spy)

    bad = await git.create_worktree("p", git.CreateWorktreeRequest(name="../x"))
    assert bad["success"] is False

    ok = await git.create_worktree("p", git.CreateWorktreeRequest(name="ok-1"))
    assert ok["success"] is True
    assert (repo / ".tfactory" / "worktrees" / "tasks" / "ok-1").is_dir()
    assert seen == ["ok-1"]
