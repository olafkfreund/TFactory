"""RFC-0007 / PARR seam (#87 / handoff fix): ingest self-registers via git_url."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from server.routes import specs as specs_mod  # noqa: E402
from server.routes.specs import SpecIngestRequest, ingest_spec  # noqa: E402


@pytest.mark.asyncio
async def test_unknown_project_with_git_url_clones_and_registers(monkeypatch, tmp_path):
    seen = {}

    async def fake_clone(*, git_url, branch, credential):
        seen.update(git_url=git_url, branch=branch, cred=credential)
        (tmp_path / ".git").mkdir(exist_ok=True)
        return tmp_path

    def fake_create_ws(**kw):
        seen["project_root"] = kw.get("project_root")
        seen["source_branch"] = kw.get("source_branch")
        seen["ref"] = kw.get("ref")
        return {"spec_dir": str(tmp_path)}

    # project store empty -> unknown project
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    monkeypatch.setattr(
        "server.routes.projects.save_projects", lambda p: seen.update(projects=p)
    )
    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", fake_clone
    )
    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.create_spec_ingest_workspace",
        fake_create_ws,
    )

    req = SpecIngestRequest(
        project_id="olafkfreund-aifactory-test",
        spec_id="027-x",
        spec_text="## Acceptance Criteria\n- it works",
        git_url="https://github.com/olafkfreund/aifactory-test.git",
        source_branch="aifactory/027-x",
    )
    out = await ingest_spec(req)
    assert out["task_id"] == "027-x"
    assert seen["git_url"].endswith("aifactory-test.git")
    assert seen["branch"] == "aifactory/027-x"
    assert seen["project_root"] == str(tmp_path)  # cloned path used as root
    assert seen["source_branch"] == "aifactory/027-x"
    assert next(iter(seen["projects"].values()))["name"] == "aifactory-test"


@pytest.mark.asyncio
async def test_self_registered_alias_resolves_second_ingest_without_clone(
    monkeypatch, tmp_path
):
    projects = {}
    calls = []

    async def clone(*, git_url, branch, credential):
        calls.append(git_url)
        (tmp_path / ".git").mkdir(exist_ok=True)
        return tmp_path

    monkeypatch.setattr("server.routes.projects.load_projects", lambda: projects.copy())
    monkeypatch.setattr(
        "server.routes.projects.save_projects", lambda data: projects.update(data)
    )
    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", clone
    )
    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.create_spec_ingest_workspace",
        lambda **kw: {"spec_dir": str(tmp_path)},
    )
    first = SpecIngestRequest(
        project_id="sender-id",
        spec_id="first",
        spec_text="## Acceptance Criteria\n- works",
        git_url="https://github.com/o/repo.git",
    )
    second = SpecIngestRequest(
        project_id="sender-id",
        spec_id="second",
        spec_text="## Acceptance Criteria\n- works",
        no_register=True,
    )
    assert (await ingest_spec(first))["task_id"] == "first"
    assert next(iter(projects.values()))["name"] == "repo"
    assert next(iter(projects.values()))["aliases"] == ["sender-id"]
    assert (await ingest_spec(second))["task_id"] == "second"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_no_register_rejects_unknown_project_before_clone(monkeypatch):
    from unittest.mock import AsyncMock

    clone = AsyncMock(side_effect=AssertionError("must not clone"))
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", clone
    )
    req = SpecIngestRequest(
        project_id="unknown",
        spec_id="s1",
        spec_text="## Acceptance Criteria\n- works",
        repo="owner/repo",
        no_register=True,
    )
    with pytest.raises(HTTPException) as error:
        await ingest_spec(req)
    assert (error.value.status_code, error.value.detail) == (
        404,
        "Project not found",
    )
    clone.assert_not_awaited()


@pytest.mark.asyncio
async def test_ref_with_source_branch_is_400(monkeypatch, tmp_path):
    """#1416: both fields is a caller error, not a silently dropped ref. The
    real ingest runs, so this also proves ``ref`` reaches it (#1404)."""

    async def fake_clone(*, git_url, branch, credential):
        (tmp_path / ".git").mkdir(exist_ok=True)
        return tmp_path

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    monkeypatch.setattr("server.routes.projects.save_projects", lambda p: None)
    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", fake_clone
    )
    req = SpecIngestRequest(
        project_id="olafkfreund-aifactory-test",
        spec_id="027-x",
        spec_text="## Acceptance Criteria\n- it works",
        git_url="https://github.com/olafkfreund/aifactory-test.git",
        source_branch="aifactory/027-x",
        ref="v1.2.0",
    )
    with pytest.raises(HTTPException) as ei:
        await ingest_spec(req)
    assert ei.value.status_code == 400
    assert "either ref or source_branch" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_unknown_project_without_git_url_still_404(monkeypatch):
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    req = SpecIngestRequest(
        project_id="nope", spec_id="1", spec_text="## Acceptance Criteria\n- x"
    )
    with pytest.raises(HTTPException) as ei:
        await ingest_spec(req)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_ingest_forwards_originator_and_agent_identity(monkeypatch, tmp_path):
    """Factory#1960: the route hands both to the workspace writer."""
    seen = {}

    def fake_create_ws(**kw):
        seen.update(kw)
        return {"spec_dir": str(tmp_path)}

    monkeypatch.setattr(
        "server.routes.projects.load_projects",
        lambda: {"proj": {"name": "proj", "path": str(tmp_path)}},
    )
    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.create_spec_ingest_workspace",
        fake_create_ws,
    )

    async def fake_ensure(entry, pid, source_branch=None):
        return tmp_path

    monkeypatch.setattr("server.routes.specs._ensure_project_clone", fake_ensure)
    await ingest_spec(
        SpecIngestRequest(
            project_id="proj",
            spec_id="s1",
            spec_text="## Acceptance Criteria\n- it works",
            originator="github:octocat",
            agent_identity="agent:aifactory/k1",
        )
    )
    assert seen["originator"] == "github:octocat"
    assert seen["agent_identity"] == "agent:aifactory/k1"


@pytest.mark.parametrize("field", ["originator", "agent_identity"])
@pytest.mark.parametrize("bad", ["x" * 129, "a\nb", "a\n", 'a"b', ""])
def test_provenance_fields_reject_unbounded_or_odd_values(field, bad):
    """Factory#1960 review: provenance is bounded and charset-limited."""
    from pydantic import ValidationError

    base = {
        "project_id": "p",
        "spec_id": "s",
        "spec_text": "## Acceptance Criteria\n- x",
    }
    with pytest.raises(ValidationError):
        SpecIngestRequest(**base, **{field: bad})


def _specs_client(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "README.md").write_text("x")
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setattr(
        "server.routes.projects.load_projects",
        lambda: {"proj": {"name": "proj", "path": str(repo)}},
    )
    monkeypatch.setattr(specs_mod, "require_project_role", AsyncMock())
    app = FastAPI()
    app.include_router(specs_mod.router)
    app.dependency_overrides[specs_mod.get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize(
    ("spec_id", "correlation_key"),
    [("s-issue", "o/r#42"), ("s-pf", "pf-337-x")],
)
def test_ingest_route_writes_correlation_key_to_source_json(
    monkeypatch, tmp_path, spec_id, correlation_key
):
    import json

    client = _specs_client(monkeypatch, tmp_path)
    response = client.post(
        "/api/specs/ingest",
        json={
            "project_id": "proj",
            "spec_id": spec_id,
            "spec_text": "## Acceptance Criteria\n- it works",
            "correlation_key": correlation_key,
        },
    )
    assert response.status_code == 200, response.text
    source_path = (
        tmp_path
        / "ws"
        / "workspaces"
        / "proj"
        / "specs"
        / spec_id
        / "context"
        / "source.json"
    )
    source = json.loads(source_path.read_text())
    assert source["correlation_key"] == correlation_key


@pytest.mark.parametrize("bad_key", ["x" * 129, "pf 337", "12345"])
def test_ingest_route_rejects_invalid_correlation_key_with_422(
    monkeypatch, tmp_path, bad_key
):
    client = _specs_client(monkeypatch, tmp_path)
    response = client.post(
        "/api/specs/ingest",
        json={
            "project_id": "proj",
            "spec_id": "s1",
            "spec_text": "## Acceptance Criteria\n- it works",
            "correlation_key": bad_key,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("blank", ["", "   "])
def test_spec_ingest_request_blank_correlation_key_is_none(blank: str) -> None:
    # #1713 review: "" means "no key", never a 422.
    req = SpecIngestRequest(
        project_id="p", spec_id="s", spec_text="x", correlation_key=blank
    )
    assert req.correlation_key is None
