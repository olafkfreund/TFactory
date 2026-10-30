"""Task-route RBAC dependency (TFactory#1450; mirrors AIFactory project_authz)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

# (module, method, path, role) — the 20 task write routes that carry the dependency.
_W = "/{task_id}/worktree/"
EXPECTED = [
    *[
        ("execution", "POST", f"/{{task_id}}/{p}", "member")
        for p in ("start", "stop", "recover")
    ],
    ("tasks", "PATCH", "/{task_id}/status", "member"),
    ("tasks", "PUT", "/{task_id}", "member"),
    ("tasks", "PATCH", "/{task_id}", "member"),
    ("tasks", "DELETE", "/{task_id}", "member"),
    *[
        ("tasks_clarifications", "POST", f"/{{task_id}}/{p}", "member")
        for p in ("clarifications", "clarifications/answers")
    ],
    *[
        ("tasks_logs", "POST", f"/{{task_id}}/logs/{p}", "viewer")
        for p in ("watch", "unwatch")
    ],
    *[
        ("tasks_plan", "POST", f"/{{task_id}}/{p}", "member")
        for p in ("approve-plan", "reject-plan")
    ],
    *[
        ("tasks_worktree", "POST", _W + p, "member")
        for p in (
            "resolve-conflicts",
            "resolve-uncommitted",
            "resolve-git-merge",
            "abort-merge",
            "create-pr",
            "merge",
            "discard",
        )
    ],
]

EXPECTED_PROJECT_ROUTES = [
    ("POST", "/{project_id}/tasks", "member"),
    ("POST", "/{project_id}/tasks/{spec_id}/logs/watch", "viewer"),
    ("POST", "/{project_id}/tasks/{spec_id}/logs/unwatch", "viewer"),
    ("POST", "/{project_id}/tasks/archive", "member"),
    ("POST", "/{project_id}/tasks/unarchive", "member"),
]

PROJECTS = {"p1": {"org_id": "A"}, "legacy": {}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """execution router on SQLite: org A has viewer-a, member-a."""
    from server.config import get_settings
    from server.database.models import Base, Organization, OrgMember, User
    from server.routes import execution, project_authz
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "member-a", "owner"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            db.add(Organization(id="A", name="a", slug="a", owner_id="owner"))
            await db.flush()
            db.add(OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            db.add(OrgMember(org_id="A", user_id="member-a", role="member"))
            await db.commit()

    asyncio.run(_init())

    app = FastAPI()
    app.include_router(execution.router, prefix="/api/tasks")

    rollbacks: list[bool] = []

    async def _db():
        async with session_local() as session:
            orig_rollback = session.rollback

            async def _tracked_rollback():
                rollbacks.append(True)
                return await orig_rollback()

            monkeypatch.setattr(session, "rollback", _tracked_rollback)
            yield session

    app.dependency_overrides[project_authz.get_db] = _db

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        user = {"id": request.headers["X-Test-Principal"], "role": "user"}
        if role := request.headers.get("X-Test-Jwt-Role"):
            user["role"] = role
        if "X-Test-Service" in request.headers:
            user["is_service"] = True
        if key_org := request.headers.get("X-Test-Key-Org"):
            user.update(api_key_id="k1", org_id=key_org)
        request.state.user = user
        return await call_next(request)

    settings = get_settings()
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    monkeypatch.setattr(settings, "RBAC_ENFORCE_TASK_ROUTES", False)
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: PROJECTS)

    audits: list[dict] = []

    async def _audit(**kw):
        audits.append(kw)

    monkeypatch.setattr("server.services.audit_service.log_audit_event_bg", _audit)

    class Env:
        pass

    e = Env()
    e.settings = settings
    e.audits = audits
    e.rollbacks = rollbacks
    e.client = TestClient(app)
    e.monkeypatch = monkeypatch

    def call(principal, task="p1:s1", path="stop", **headers):
        return e.client.post(
            f"/api/tasks/{task}/{path}",
            headers={"X-Test-Principal": principal, **headers},
        )

    e.call = call
    with e.client:
        yield e
    asyncio.run(engine.dispose())


def _enforce(env, on=True):
    env.monkeypatch.setattr(env.settings, "RBAC_ENFORCE_TASK_ROUTES", on)


def test_enforced_viewer_forbidden_on_start(env):
    _enforce(env)
    r = env.call("viewer-a", path="start", **{"Content-Type": "application/json"})
    assert r.status_code == 403
    assert env.audits[0]["action"] == "authz.task.denied"


def test_shadow_viewer_gets_handler_response(env):
    r = env.call("viewer-a")
    assert (r.status_code, r.json()["detail"]) == (404, "Task is not running")


def test_enforced_member_passes(env):
    _enforce(env)
    assert env.call("member-a").json()["detail"] == "Task is not running"


def test_enforced_service_passes(env):
    _enforce(env)
    r = env.call("svc", task="nope:s1", **{"X-Test-Service": "1"})
    assert r.json()["detail"] == "Task is not running"


def test_enforced_key_bound_to_other_org_forbidden(env):
    _enforce(env)
    assert env.call("member-a", **{"X-Test-Key-Org": "B"}).status_code == 403


def test_enforced_jwt_admin_claim_without_membership_forbidden(env):
    _enforce(env)
    assert env.call("stranger", **{"X-Test-Jwt-Role": "admin"}).status_code == 403


def test_enforced_unowned_project_forbidden(env):
    _enforce(env)
    assert env.call("member-a", task="legacy:s1").status_code == 403


def test_shadow_unknown_project_same_as_without_dependency(env):
    shadow = env.call("member-a", task="nope:s1")
    env.monkeypatch.setattr(env.settings, "DISABLE_AUTH", True)  # checker no-ops
    baseline = env.call("member-a", task="nope:s1")
    assert (shadow.status_code, shadow.json()) == (
        baseline.status_code,
        baseline.json(),
    )
    assert shadow.json()["detail"] == "Task is not running"


def test_shadow_lookup_failure_fails_open(env):
    def _boom():
        raise RuntimeError("projects.json unreadable")

    env.monkeypatch.setattr("server.routes.projects.load_projects", _boom)
    r = env.call("viewer-a")
    assert (r.status_code, r.json()["detail"]) == (404, "Task is not running")


def test_enforced_lookup_failure_propagates(env):
    _enforce(env)

    def _boom():
        raise RuntimeError("projects.json unreadable")

    env.monkeypatch.setattr("server.routes.projects.load_projects", _boom)
    with pytest.raises(RuntimeError):
        env.call("viewer-a")


def test_shadow_would_deny_is_audited_and_logged(env, caplog):
    with caplog.at_level("WARNING"):
        env.call("viewer-a")
    assert [a["action"] for a in env.audits] == ["authz.task.would_deny"]
    assert env.audits[0]["resource_id"] == "p1"
    assert "would deny" in caplog.text


def test_shadow_allowed_writes_no_audit(env):
    env.call("member-a")
    assert env.audits == []


def test_session_rolled_back_after_allowed_member_call(env):
    _enforce(env)
    env.call("member-a")
    assert env.rollbacks == [True]


def test_session_rolled_back_on_enforced_deny(env):
    _enforce(env)
    assert env.call("viewer-a").status_code == 403
    assert env.rollbacks == [True]


def test_every_task_write_route_carries_member_dependency():
    import importlib

    from server.routes.project_authz import TaskAccessChecker

    missing = []
    for module, method, path, role in EXPECTED:
        router = importlib.import_module(f"server.routes.{module}").router
        found = [r for r in router.routes if r.path == path and method in r.methods]
        ok = bool(found) and all(
            any(
                isinstance(d.dependency, TaskAccessChecker)
                and d.dependency.minimum_role == role
                for d in r.dependencies
            )
            for r in found
        )
        if not ok:
            missing.append((module, method, path, role))
    assert not missing
    assert len(EXPECTED) == 20


def test_every_project_task_route_carries_project_access_dependency():
    from server.routes import projects
    from server.routes.project_authz import ProjectAccessChecker

    missing = []
    for method, path, role in EXPECTED_PROJECT_ROUTES:
        found = [
            r for r in projects.router.routes if r.path == path and method in r.methods
        ]
        ok = bool(found) and all(
            any(
                isinstance(d.dependency, ProjectAccessChecker)
                and d.dependency.minimum_role == role
                for d in r.dependencies
            )
            for r in found
        )
        if not ok:
            missing.append((method, path, role))
    assert not missing
    assert len(EXPECTED_PROJECT_ROUTES) == 5
