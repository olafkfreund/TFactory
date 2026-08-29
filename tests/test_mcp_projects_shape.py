"""``projects.json`` shape agreement + named tool errors.

Two writers share ``~/.tfactory/projects.json``: the web-server's
``JsonProjectStore``, which owns the canonical id-keyed map, and these MCP
task-control tools, which read (and wrote) a ``{"projects": [...]}`` envelope.
Against a real, portal-written file every tool that touched the registry raised
``KeyError('projects')`` and the MCP transport rendered it as the bare string
``'projects'`` — a shape bug that read like a data bug.

These tests pin both halves: the reader accepts the map, and an exception that
escapes a tool arrives carrying its type name.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from agents.tools_pkg.tools.task_control import (  # noqa: E402
    ProjectsFileError,
    _load_projects,
    _save_projects,
)

# The map shape the portal writes, trimmed to the keys that matter here.
PORTAL_SHAPE = {
    "4126a206-946a-4d5e-9f44-eec2ce84ea3f": {
        "path": "/home/dev/checkouts/sarc",
        "name": "sarc",
        "settings": {"mainBranch": "main"},
    }
}


def test_reads_the_portals_id_keyed_map(tmp_path):
    (tmp_path / "projects.json").write_text(json.dumps(PORTAL_SHAPE))

    entries = _load_projects(tmp_path)["projects"]

    assert [e["id"] for e in entries] == ["4126a206-946a-4d5e-9f44-eec2ce84ea3f"]
    assert entries[0]["name"] == "sarc"
    # ``path`` (portal) must surface under the tools' ``root_path``.
    assert entries[0]["root_path"] == "/home/dev/checkouts/sarc"
    assert entries[0]["settings"] == {"mainBranch": "main"}


def test_still_reads_the_legacy_envelope(tmp_path):
    (tmp_path / "projects.json").write_text(
        json.dumps({"projects": [{"id": "p1", "name": "n", "root_path": "/x"}]})
    )

    entries = _load_projects(tmp_path)["projects"]

    assert [e["id"] for e in entries] == ["p1"]
    assert entries[0]["root_path"] == "/x"


def test_missing_file_is_an_empty_registry(tmp_path):
    assert _load_projects(tmp_path) == {"projects": []}


def test_unreadable_file_is_not_reported_as_empty(tmp_path):
    """ "Cannot parse" must not collapse into "no projects registered"."""
    (tmp_path / "projects.json").write_text("{ not json")

    with pytest.raises(ProjectsFileError) as excinfo:
        _load_projects(tmp_path)

    assert "JSONDecodeError" in str(excinfo.value)


def test_unknown_shape_names_what_it_found(tmp_path):
    (tmp_path / "projects.json").write_text('"a bare string"')

    with pytest.raises(ProjectsFileError) as excinfo:
        _load_projects(tmp_path)

    assert "str" in str(excinfo.value)


def test_save_round_trips_through_the_portal_shape(tmp_path):
    """A tool-written registry must be readable by the portal, and back."""
    _save_projects(
        {"projects": [{"id": "p1", "name": "n", "root_path": "/x"}]}, tmp_path
    )

    on_disk = json.loads((tmp_path / "projects.json").read_text())
    assert list(on_disk) == ["p1"]
    assert on_disk["p1"]["path"] == "/x"  # the key JsonProjectStore consumers read

    assert _load_projects(tmp_path)["projects"][0]["root_path"] == "/x"


# ---------------------------------------------------------------------------
# Error surfacing
# ---------------------------------------------------------------------------


def test_escaping_exception_reaches_the_caller_named():
    """A KeyError must not arrive as the bare string ``'projects'``."""
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk import tool
    from mcp_server.tfactory_server import _named_errors

    @tool("boom", "raises", {"type": "object"})
    async def boom(args):
        raise KeyError("projects")

    result = asyncio.run(_named_errors(boom).handler({}))

    text = result["content"][0]["text"]
    assert result["is_error"] is True
    assert "KeyError" in text, f"error does not name its type: {text!r}"
    assert text != "'projects'"


def test_named_errors_leaves_a_successful_tool_alone():
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk import tool
    from mcp_server.tfactory_server import _named_errors

    @tool("ok", "works", {"type": "object"})
    async def ok(args):
        return {"content": [{"type": "text", "text": "fine"}]}

    wrapped = _named_errors(ok)

    assert wrapped.name == "ok"
    assert asyncio.run(wrapped.handler({}))["content"][0]["text"] == "fine"


def test_tool_errors_carry_the_key_the_sdk_actually_reads():
    """``create_sdk_mcp_server`` builds its wire result from ``is_error``.

    Its adapter does ``"isError": result.get("is_error", False)``, so a tool
    returning only the camelCase ``isError`` reached the model as a SUCCESS
    whose text happened to begin with "Error:". Both spellings are required:
    snake for the wire, camel for the call sites that assert on the handler's
    own return value. Reaching the adapter directly is mcp-major-specific, so
    this pins the contract at the shape we control.
    """
    from agents.tools_pkg.tools.task_control import _format_error

    err = _format_error("nope")

    assert err["is_error"] is True
    assert err["isError"] is True
