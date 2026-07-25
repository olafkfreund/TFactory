"""Capability-discovery manifest — GET /.well-known/agent-skills/index.json (RFC-0019 §3.4).

The entry point an external agent hits *first*: it enumerates what TFactory can
do before it knows anything about this service, and before it holds a
credential. So this endpoint is deliberately readable without authentication.

That is safe because the payload is public metadata only — service identity +
version, the capability names/descriptions already published in this repo and by
the MCP tool catalog, and relative paths to the MCP + OpenAPI surfaces. No
tokens, no internal/in-cluster hostnames, no task or workspace data.

``TokenAuthMiddleware`` guards paths under ``/api`` only (see
``server/auth.py``), so this path is exempt *by construction* rather than by an
added exception — like the SPA routes and ``/openapi.json``. A test asserts that
with auth ENFORCED, so a future widening of the guarded prefixes fails there
rather than silently locking agents out of discovery.

Where the two halves of ``skills`` come from
--------------------------------------------
*MCP tools* are derived from :data:`server.routes.mcp_copilot._TOOLS` — the tool
list the always-mounted ``POST /mcp`` server really advertises — so that half
cannot drift from the server. ``/api/mcp-remote`` is deliberately NOT the
advertised endpoint: it only mounts when ``TFACTORY_MCP_REMOTE_ENABLED`` is set,
and a manifest that names an endpoint which 404s half the time is worse than one
that names the modest endpoint that is always there.

*Installable skills* are a curated constant rather than a scan of
``.claude/skills/``: ``.dockerignore`` excludes ``.claude/`` from the image, so a
runtime scan would publish a full list in dev and an empty one in production.
The constant is pinned to reality instead by ``tests/test_well_known_agent_skills.py``,
which asserts every ``install.path`` exists and its ``SKILL.md`` declares the
same ``name``. Curated, per the contract: repo-authoring skills (``/blog``,
``/demo``) are omitted — they produce content *about* TFactory rather than being
capabilities an external agent would invoke.

URLs are origin-relative wherever the consumer already knows the origin; the
only absolute URLs are the public GitHub repo the skills install from.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .mcp_copilot import _TOOLS as _MCP_TOOLS

router = APIRouter(tags=["well-known"])

WELL_KNOWN_AGENT_SKILLS_PATH = "/.well-known/agent-skills/index.json"

REPOSITORY_URL = "https://github.com/olafkfreund/TFactory"

_SERVICE_DESCRIPTION = (
    "Autonomous test generation and execution. From a branch or PR plus its "
    "acceptance criteria it runs planner -> gen-functional -> executor "
    "(sandboxed) -> evaluator -> triager across five lanes (unit, browser, api, "
    "integration, mutation). Every generated test is graded on a 5-signal "
    "verdict - coverage delta, stability across re-runs, mutation score, flake "
    "lint and semantic relevance - so a pass means meaningful tests, not a "
    "green bar."
)

# Installable skill packages (SKILL.md directories in this repo). Each entry is
# reviewed, and `tests/test_well_known_agent_skills.py` pins it to the file on
# disk so a rename or deletion fails CI instead of shipping a manifest that lies.
_INSTALLABLE_SKILLS: list[dict[str, Any]] = [
    {
        "name": "handover-to-tfactory",
        "description": (
            "Hand a finished feature off to TFactory for polyglot test "
            "generation - from AIFactory, from Claude Code, or from any tool "
            "via the MCP control plane or a markdown/Gherkin/EARS "
            "acceptance-criteria file. Drives the full pipeline across the five "
            "lanes and produces a triage report, optionally committing the "
            "tests to the feature branch and commenting on the PR."
        ),
        "invocation": {"kind": "slash_command", "command": "/handover-to-tfactory"},
        "install": {"path": ".claude/skills/handover-to-tfactory/SKILL.md"},
        "tags": ["verify", "handover", "parr"],
    },
    {
        "name": "tfactory-watch",
        "description": (
            "Poll a running TFactory task to completion, then pick up its "
            "triage report and check the generated tests actually cover the "
            "acceptance criteria. One check per invocation - drive it under "
            "/loop for a hands-off round trip."
        ),
        "invocation": {"kind": "slash_command", "command": "/tfactory-watch"},
        "install": {"path": ".claude/skills/tfactory-watch/SKILL.md"},
        "tags": ["polling", "reports"],
    },
    {
        "name": "handback-to-aifactory",
        "description": (
            "When the tests find problems in a feature, hand a correction back "
            "to AIFactory for revision: the failing tests plus the report, so "
            "AIFactory's QA fixer works on the original spec. Previews the "
            "target spec and sends only on confirmation."
        ),
        "invocation": {"kind": "slash_command", "command": "/handback-to-aifactory"},
        "install": {"path": ".claude/skills/handback-to-aifactory/SKILL.md"},
        "tags": ["handback", "loop", "parr"],
    },
    {
        "name": "tfactory-fixloop",
        "description": (
            "Close the AIFactory <-> TFactory loop hands-off: hand the "
            "correction back, wait for AIFactory's QA fixer, re-test. One "
            "bounded cycle per invocation; it stops when the run passes, when "
            "the same tests keep failing (no progress), or when the "
            "correction-cycle cap is hit and a human is needed."
        ),
        "invocation": {"kind": "slash_command", "command": "/tfactory-fixloop"},
        "install": {"path": ".claude/skills/tfactory-fixloop/SKILL.md"},
        "tags": ["loop", "handback", "autonomous"],
    },
    {
        "name": "tfactory-init",
        "description": (
            "Scaffold the .tfactory.yml and an empty tests catalog in a "
            "repository so TFactory can start generating tests for it. Run this "
            "once per repo, before the first handover."
        ),
        "invocation": {"kind": "slash_command", "command": "/tfactory-init"},
        "install": {"path": ".claude/skills/tfactory-init/SKILL.md"},
        "tags": ["onboarding", "scaffold"],
    },
    {
        "name": "tfactory-add-test",
        "description": (
            "Generate ONE test for a specific function or file, without running "
            "the whole pipeline. The quick 'give me a starting test for this' "
            "path when a full branch handover would be overkill."
        ),
        "invocation": {"kind": "slash_command", "command": "/tfactory-add-test"},
        "install": {"path": ".claude/skills/tfactory-add-test/SKILL.md"},
        "tags": ["generation", "single-test"],
    },
    {
        "name": "tfactory-from-template",
        "description": (
            "Emit a ready-to-run test file from one of the starter templates "
            "(five each for pytest, Jest and Playwright), with its variables "
            "filled in. Deterministic - no model call involved."
        ),
        "invocation": {"kind": "slash_command", "command": "/tfactory-from-template"},
        "install": {"path": ".claude/skills/tfactory-from-template/SKILL.md"},
        "tags": ["templates", "generation", "deterministic"],
    },
    {
        "name": "cloud-discover",
        "description": (
            "Run a read-only cloud posture assessment of an AWS, GCP or Azure "
            "account: discover the resources, scan for misconfigurations with "
            "Prowler (CIS/OCSF), draw the service topology, and emit an "
            "accept/flag/reject verdict plus a remediation plan. This is cloud "
            "posture, not application security scanning."
        ),
        "invocation": {"kind": "slash_command", "command": "/cloud-discover"},
        "install": {"path": ".claude/skills/cloud-discover/SKILL.md"},
        "tags": ["cloud", "posture", "cspm", "read-only"],
    },
    {
        "name": "tfactory",
        "description": (
            "Onboarding skill for working inside the TFactory codebase itself - "
            "the 4-agent pipeline, the LLM-provider abstraction (never call a "
            "vendor SDK directly), the workspace layout, the branching model "
            "and how to run the suite. Install this before changing TFactory, "
            "not before using it."
        ),
        "invocation": {"kind": "slash_command", "command": "/tfactory"},
        "install": {"path": "docs/.well-known/skills/tfactory/SKILL.md"},
        "tags": ["onboarding", "codebase", "contributors"],
    },
]


def _mcp_tool_skills() -> list[dict[str, Any]]:
    """Skill entries for the tools ``POST /mcp`` really advertises.

    Derived from the server's own tool list so the manifest cannot drift from
    it. Tool ids are ``tfactory_get_spec``-style; skill names must match
    ``^[a-z][a-z0-9-]{1,63}$``, so the redundant prefix is dropped and
    underscores become hyphens (``get-spec``). The tool id an agent actually
    calls is carried verbatim in ``invocation.tool``.
    """
    return [
        {
            "name": tool["name"].removeprefix("tfactory_").replace("_", "-"),
            "description": tool["description"],
            "invocation": {"kind": "mcp_tool", "tool": tool["name"]},
            "tags": ["mcp", "task-data"],
        }
        for tool in _MCP_TOOLS
    ]


def _manifest(version: str, openapi_url: str) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "kind": "service",
        "service": {
            "name": "tfactory",
            "title": "TFactory",
            "version": version,
            "role": "reflect",
            "description": _SERVICE_DESCRIPTION,
            "repository": REPOSITORY_URL,
        },
        "openapi_url": openapi_url,
        # POST-only MCP JSON-RPC, always mounted (see routes/mcp_copilot.py).
        "mcp": {"transport": "streamable-http", "endpoint": "/mcp"},
        "auth": {"manifest": "none", "schemes": ["bearer"]},
        "skills": [
            {**skill, "install": {"source": REPOSITORY_URL, **skill["install"]}}
            for skill in _INSTALLABLE_SKILLS
        ]
        + _mcp_tool_skills(),
    }


@router.get(WELL_KNOWN_AGENT_SKILLS_PATH)
async def agent_skills_index(request: Request) -> JSONResponse:
    """Serve this service's agent-skills manifest, unauthenticated."""
    return JSONResponse(
        # The running service version, read from the app FastAPI was built with,
        # so it tracks the release bump without a second source of truth.
        _manifest(request.app.version, request.app.openapi_url or "/openapi.json"),
        headers={
            # Public, small, changes only on deploy (contract §5).
            "Cache-Control": "public, max-age=300",
            # A browser-based agent is a first-class consumer (contract §2); the
            # app's CORS middleware is credentialed and so never emits "*".
            "Access-Control-Allow-Origin": "*",
        },
    )
