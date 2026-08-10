"""Tests for the agent capability manifest (RFC-0019 §3.4, #302).

GET /.well-known/agent-skills/index.json. Four properties matter:

1. it validates against the Factory contract's `kind: "service"` shape
   (apis/agent-skills-manifest.schema.json in the Factory hub);
2. it is readable WITHOUT a credential — agents enumerate capabilities before
   they hold a token. Asserted with auth ENFORCED: CI runs the suite with
   APP_DISABLE_AUTH=true, under which every path is open and the check would
   prove nothing, so the settings singleton is flipped back for that test;
3. it leaks nothing — public metadata only, no tokens, no in-cluster hostnames;
4. every advertised installable skill exists on disk under the name it claims,
   so the curated list cannot drift from the SKILL.md files it points at.

Skipped automatically in venvs without FastAPI (see tests/conftest.py).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
# Make apps/web-server importable for `server.routes.well_known`.
_WEB_SERVER = _REPO_ROOT / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server import config  # noqa: E402
from server.auth import TokenAuthMiddleware  # noqa: E402
from server.routes import well_known  # noqa: E402
from server.routes.mcp_copilot import _TOOLS as MCP_TOOLS  # noqa: E402

PATH = well_known.WELL_KNOWN_AGENT_SKILLS_PATH

# Contract patterns (agent-skills-manifest.schema.json).
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_COMMAND_RE = re.compile(r"^/[a-z0-9][a-z0-9-]*$")
_URL_RE = re.compile(r"^(https?://|/)")


def _app() -> FastAPI:
    """The manifest router behind the real auth middleware, plus a guarded route.

    The guarded route stands in for the /api surface: it is what proves the
    middleware is actually enforcing, so the manifest's 200 means something.
    """
    app = FastAPI(version="9.9.9-test")
    app.add_middleware(TokenAuthMiddleware)
    app.include_router(well_known.router)

    @app.get("/api/guarded")
    async def guarded() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


@pytest.fixture
def enforced_auth(monkeypatch) -> None:
    """Force auth ON regardless of the suite-wide APP_DISABLE_AUTH=true."""
    monkeypatch.setattr(config.settings, "DISABLE_AUTH", False)


def test_manifest_matches_the_service_contract(client):
    resp = client.get(PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    body = resp.json()
    assert body["schema_version"] == "1"
    assert body["kind"] == "service"

    service = body["service"]
    assert service["name"] == "tfactory"
    assert _NAME_RE.match(service["name"])
    assert service["role"] == "reflect"
    # The running service version, not a second hardcoded copy.
    assert service["version"] == "9.9.9-test"

    assert _URL_RE.match(body["openapi_url"])
    # An HTTP transport MUST carry an endpoint (schema: mcp_endpoint.allOf).
    assert body["mcp"]["transport"] in {"stdio", "http+sse", "streamable-http"}
    assert _URL_RE.match(body["mcp"]["endpoint"])
    # Pinned constant so a consumer can assert the no-auth guarantee.
    assert body["auth"]["manifest"] == "none"


def test_every_skill_entry_is_well_formed(client):
    skills = client.get(PATH).json()["skills"]
    assert len(skills) >= 1  # schema: minItems 1

    names = [s["name"] for s in skills]
    assert len(names) == len(set(names)), "skill names must be unique"

    for skill in skills:
        assert _NAME_RE.match(skill["name"]), skill["name"]
        assert skill["description"].strip()
        invocation = skill["invocation"]
        if invocation["kind"] == "slash_command":
            assert _COMMAND_RE.match(invocation["command"])
        elif invocation["kind"] == "mcp_tool":
            assert invocation["tool"]
        else:  # pragma: no cover - no rest entries today
            pytest.fail(f"unexpected invocation kind: {invocation['kind']}")
        if "install" in skill:
            assert _URL_RE.match(skill["install"]["source"])
            assert skill["install"]["path"]


def test_mcp_skills_are_derived_from_the_live_tool_catalog(client):
    """The MCP half mirrors what POST /mcp advertises, so it cannot drift."""
    skills = client.get(PATH).json()["skills"]
    advertised = {
        s["invocation"]["tool"] for s in skills if s["invocation"]["kind"] == "mcp_tool"
    }
    assert advertised == {tool["name"] for tool in MCP_TOOLS}


def test_installable_skills_exist_on_disk_under_the_name_they_claim():
    """Pins the curated list to reality: a rename or deletion fails here.

    The payload can't scan .claude/skills at runtime (.dockerignore strips it
    from the image), so the correspondence is enforced at CI time instead.
    """
    for skill in well_known._INSTALLABLE_SKILLS:
        path = _REPO_ROOT / skill["install"]["path"]
        assert path.is_file(), f"{skill['name']}: missing {skill['install']['path']}"
        frontmatter = path.read_text(encoding="utf-8")[:400]
        assert f"name: {skill['name']}\n" in frontmatter, (
            f"{skill['name']}: SKILL.md declares a different name"
        )


def test_manifest_leaks_nothing_sensitive(client):
    raw = client.get(PATH).text.lower()
    for leak in ("token", "secret", "api_key", "apikey", "password", "credential"):
        assert leak not in raw
    # No in-cluster or internal hosts (cf. CFACTORY_SEARCH_URL in config.py).
    for host in ("svc.cluster.local", "localhost", "127.0.0.1", "0.0.0.0"):  # noqa: S104 - grepping for a leak, not binding
        assert host not in raw
    # The only absolute URLs are the public repo the skills install from.
    assert set(re.findall(r"https?://[^/\"]+", raw)) <= {"https://github.com"}


@pytest.mark.usefixtures("enforced_auth")
def test_manifest_is_readable_without_a_credential(client):
    # The guarded surface rejects an unauthenticated caller ...
    assert client.get("/api/guarded").status_code == 401
    # ... while discovery stays open.
    resp = client.get(PATH)
    assert resp.status_code == 200
    assert resp.json()["kind"] == "service"
    # And a browser-based agent can read it cross-origin (contract §2).
    assert resp.headers["access-control-allow-origin"] == "*"


def test_manifest_is_cacheable(client):
    assert client.get(PATH).headers["cache-control"] == "public, max-age=300"
