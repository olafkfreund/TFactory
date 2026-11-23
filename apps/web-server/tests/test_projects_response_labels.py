"""Project names, route slugs, and safe repository labels."""

import asyncio
import sys
from pathlib import Path

import pytest

for root in (
    Path(__file__).resolve().parents[1],
    Path(__file__).resolve().parents[2] / "backend",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from server.routes import projects  # noqa: E402
from server.services import project_workspace_service as workspace  # noqa: E402


@pytest.mark.parametrize(
    "explicit,expected", [(None, "widget"), ("Custom App", "Custom App")]
)
def test_projects_clone_name_and_slug(tmp_path, monkeypatch, explicit, expected):
    store: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(projects, "load_projects", lambda: store)
    monkeypatch.setattr(projects, "save_projects", store.update)
    monkeypatch.setattr(workspace, "workspace_root", lambda: tmp_path)

    async def clone_or_update(**kwargs):
        path = tmp_path / workspace.slug_from_git_url(kwargs["git_url"])
        path.mkdir()
        return path

    monkeypatch.setattr(workspace, "clone_or_update", clone_or_update)
    response = asyncio.run(
        projects.create_project(
            projects.ProjectCreate(
                gitUrl="https://owner:token@git.example/acme/widget.git", name=explicit
            )
        )
    )
    assert response["name"] == expected
    assert response["slug"] == expected.lower().replace(" ", "-")
    assert response["repoLabel"] == "acme/widget"
    assert "token" not in str(response)


def test_projects_same_basename_different_repos(tmp_path):
    a = projects.project_to_response(
        "a",
        {
            "path": str(tmp_path / "a"),
            "name": "widget",
            "clonedFrom": "https://user:secret@git.example/acme/widget.git",
        },
    )
    b = projects.project_to_response(
        "b",
        {
            "path": str(tmp_path / "b"),
            "name": "widget",
            "clonedFrom": "git@git.example:other/widget.git",
        },
    )
    assert a["slug"] == b["slug"] == "widget"
    assert (a["repoLabel"], b["repoLabel"]) == ("acme/widget", "other/widget")
    assert "secret" not in str(a)


@pytest.mark.parametrize(
    "url,label,name",
    [
        ("https://git.example/owner/../repo", None, "repository"),
        ("https://git.example/owner/./repo", None, "repository"),
        ("https://git.example/./repo", None, "repository"),
        ("token:secret@host/owner/repo", "owner/repo", "repo"),
        ("token:secret/repo", None, "repository"),
        ("owner/token:secret/repo", "owner/repo", "repo"),
    ],
)
def test_repo_labels_reject_traversal_and_credentials(url, label, name):
    assert projects.repo_label(url) == label
    assert projects.repo_basename(url) == name
