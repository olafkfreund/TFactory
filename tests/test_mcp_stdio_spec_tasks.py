"""Real FastAPI checks for scoped spec-task proxy routes."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

from server.config import get_settings  # noqa: E402
from server.database.models import Base, Organization, OrgMember, User  # noqa: E402
from server.mcp_remote.auth import AuthenticatedKey  # noqa: E402
from server.mcp_stdio.auth import MCP_READ_SCOPE, TASK_WRITE_SCOPE  # noqa: E402

proxy = importlib.import_module("server.mcp_stdio.router")
from server.services.audit_service import (  # noqa: E402
    ACTION_MCP_TASK_ARCHIVE,
    ACTION_MCP_TASK_CREATE_AND_RUN,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

HEADERS = {"Authorization": "Bearer acw_test"}
PROJECTS = {
    "p1": {"name": "First", "org_id": "A"},
    "p2": {"name": "Second", "org_id": "A"},
    "foreign": {"name": "Foreign", "org_id": "B"},
}
SPEC = {
    "project_id": "p1",
    "spec_id": "s",
    "spec_text": "## Acceptance Criteria\n- works",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "root"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            for user_id in ("member", "viewer", "owner"):
                db.add(
                    User(id=user_id, email=f"{user_id}@example.test", password_hash="x")
                )
            await db.flush()
            db.add(Organization(id="A", name="a", slug="a", owner_id="owner"))
            await db.flush()
            db.add(OrgMember(org_id="A", user_id="member", role="member"))
            db.add(OrgMember(org_id="A", user_id="viewer", role="viewer"))
            await db.commit()

    asyncio.run(init())
    app = FastAPI()
    app.include_router(proxy.router)

    async def db_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[proxy.get_db] = db_override
    settings = get_settings()
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    monkeypatch.setattr(settings, "RBAC_ENFORCE_TASK_ROUTES", True)
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: PROJECTS)
    key = {}

    async def authenticate(_header):
        return key["value"]

    monkeypatch.setattr(
        "server.mcp_stdio.auth.mcp_remote_auth.authenticate", authenticate
    )
    audits = []

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(proxy, "log_audit_event_bg", audit)

    def set_key(scopes=(MCP_READ_SCOPE, TASK_WRITE_SCOPE), user="member", org="A"):
        key["value"] = AuthenticatedKey(
            key_id="k",
            scopes=frozenset(scopes),
            user_id=user,
            org_id=org,
        )

    def write_task(project, name, body):
        task_dir = tmp_path / "root/workspaces" / project / "specs" / name
        (task_dir / "context").mkdir(parents=True)
        (task_dir / "findings").mkdir()
        (task_dir / "status.json").write_text(
            json.dumps(
                {
                    "task_id": name,
                    "project_id": project,
                    "spec_id": name,
                    "status": "done",
                    "phase": "triaged",
                    "marker": body,
                }
            )
        )
        (task_dir / "context/source.json").write_text(
            json.dumps(
                {
                    "source_sha": body,
                    "repo_slug": f"owner/{project}",
                    "source_branch": "build",
                    "pr_number": 17,
                }
            )
        )
        (task_dir / "findings/triage_report.md").write_text(body)
        return task_dir

    set_key()
    with TestClient(app) as client:
        yield client, set_key, write_task, audits, monkeypatch
    asyncio.run(engine.dispose())


def test_ingest_scope_and_audit(env):
    client, set_key, _, audits, monkeypatch = env
    calls = []

    async def ingest(req, **kwargs):
        calls.append(req)
        return {"task_id": req.spec_id, "project_id": req.project_id}

    monkeypatch.setattr("server.routes.specs.ingest_spec", ingest)
    set_key(scopes={MCP_READ_SCOPE})
    assert (
        client.post(
            "/api/mcp-stdio/specs/ingest", json=SPEC, headers=HEADERS
        ).status_code
        == 403
    )
    assert calls == []
    set_key(scopes={TASK_WRITE_SCOPE})
    response = client.post("/api/mcp-stdio/specs/ingest", json=SPEC, headers=HEADERS)
    assert response.status_code == 200
    assert len(calls) == 1
    assert audits[-1]["action"] == ACTION_MCP_TASK_CREATE_AND_RUN


def test_org_key_unknown_project_is_404(env):
    client, _, _, _, monkeypatch = env
    calls = []

    async def ingest(*args, **kwargs):
        calls.append(1)

    monkeypatch.setattr("server.routes.specs.ingest_spec", ingest)
    response = client.post(
        "/api/mcp-stdio/specs/ingest",
        json={**SPEC, "project_id": "missing"},
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert calls == []


def test_read_scope_and_foreign_project_denied_by_id_and_name(env):
    client, set_key, write_task, _, _ = env
    write_task("p1", "same", "first")
    set_key(scopes={TASK_WRITE_SCOPE})
    for path in ("/api/mcp-stdio/spec-tasks", "/api/mcp-stdio/spec-tasks/p1/same"):
        assert client.get(path, headers=HEADERS).status_code == 403
    set_key()
    for project in ("foreign", "Foreign"):
        assert (
            client.get(
                f"/api/mcp-stdio/spec-tasks/{project}/same", headers=HEADERS
            ).status_code
            == 404
        )


def test_detail_viewer_role_and_project_specific_reports(env):
    client, set_key, write_task, _, _ = env
    write_task("p1", "same", "first")
    write_task("p2", "same", "second")
    set_key(user="viewer")
    for project, expected in (("p1", "first"), ("Second", "second")):
        response = client.get(
            f"/api/mcp-stdio/spec-tasks/{project}/same?report=md", headers=HEADERS
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] in ("p1", "p2")
        assert data["status_json"]["marker"] == expected
        assert data["source"]["source_sha"] == expected
        assert data["source"]["pr_number"] == 17
        assert data["report"]["body"] == expected
    listing = client.get("/api/mcp-stdio/spec-tasks", headers=HEADERS)
    assert listing.status_code == 200
    assert {(t["project_id"], t["spec_id"]) for t in listing.json()["tasks"]} == {
        ("p1", "same"),
        ("p2", "same"),
    }


def test_malformed_ids_never_touch_filesystem(env, monkeypatch):
    client, _, _, _, _ = env
    from agents.tools_pkg.tools import task_control

    calls = []
    monkeypatch.setattr(task_control, "_spec_dir", lambda *args: calls.append(args))
    for path in (
        "/api/mcp-stdio/spec-tasks/p1/%2E%2E",
        "/api/mcp-stdio/spec-tasks/%2E%2E/same",
    ):
        assert client.get(path, headers=HEADERS).status_code in (400, 404)
    assert calls == []


def test_archive_dismisses_task_and_audits(env):
    client, _, write_task, audits, _ = env
    task_dir = write_task("p1", "s", "first")
    response = client.post(
        "/api/mcp-stdio/projects/p1/tasks/archive",
        json={"taskIds": ["p1:s"]},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {"success": True, "archivedCount": 1, "errors": None}
    assert json.loads((task_dir / "status.json").read_text())["dismissed"] is True
    assert audits[-1]["action"] == ACTION_MCP_TASK_ARCHIVE


def test_archive_refuses_foreign_or_wrong_project_before_writing(env):
    client, set_key, write_task, audits, _ = env
    task_dir = write_task("p1", "s", "first")
    write_task("p2", "other", "second")
    for path, task_ids in (
        ("foreign", ["p1:s"]),
        ("missing", ["p1:s"]),
        ("p1", ["p1:s", "p2:other"]),
    ):
        response = client.post(
            f"/api/mcp-stdio/projects/{path}/tasks/archive",
            json={"taskIds": task_ids},
            headers=HEADERS,
        )
        assert response.status_code == 404
    set_key(org="B")
    assert (
        client.post(
            "/api/mcp-stdio/projects/p1/tasks/archive",
            json={"taskIds": ["p1:s"]},
            headers=HEADERS,
        ).status_code
        == 404
    )
    set_key(user="viewer")
    assert (
        client.post(
            "/api/mcp-stdio/projects/p1/tasks/archive",
            json={"taskIds": ["p1:s"]},
            headers=HEADERS,
        ).status_code
        == 403
    )
    assert "dismissed" not in json.loads((task_dir / "status.json").read_text())
    assert not any(a["action"] == ACTION_MCP_TASK_ARCHIVE for a in audits)


def test_archive_requires_task_write_scope(env):
    client, set_key, write_task, audits, _ = env
    task_dir = write_task("p1", "s", "first")
    set_key(scopes={MCP_READ_SCOPE})
    response = client.post(
        "/api/mcp-stdio/projects/p1/tasks/archive",
        json={"taskIds": ["p1:s"]},
        headers=HEADERS,
    )
    assert response.status_code == 403
    assert "dismissed" not in json.loads((task_dir / "status.json").read_text())
    assert audits == []


def test_archive_unknown_task_is_404(env):
    client, _, _, audits, _ = env
    response = client.post(
        "/api/mcp-stdio/projects/p1/tasks/archive",
        json={"taskIds": ["p1:missing"]},
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert audits == []
