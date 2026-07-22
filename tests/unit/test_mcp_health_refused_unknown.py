"""AC#5: check_mcp_health keeps its existing status:"unknown" response shape
when a URL is refused, and the specific refusal reason is logged — not returned.

This file drives the *real* SSRF guard (`_is_safe_mcp_url`) by stubbing only the
DNS resolver (`socket.getaddrinfo`), so the "unknown" answer is earned by the
production refusal path rather than by mocking the guard out. It proves two
things a refused probe must never do:

  * change the response envelope away from the pre-change
    ``{success, data:{serverId, status:"unknown", message}}`` shape, or
  * leak the concrete HTTPException ``detail`` (an SSRF oracle for internal
    reachability) into the body handed back to the client.

Target: apps/web-server/server/routes/git.py::check_mcp_health

Pure unit test — hostname resolution is mocked, so no network is touched. The
guard is import-resolved from ``server.routes.git`` (the module that actually
defines ``check_mcp_health``); ``apps/web-server`` is placed on ``sys.path`` the
same way the existing suite does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _find_web_server_root() -> Path:
    """Locate ``apps/web-server`` by walking up from this file and the cwd."""
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            web_server = candidate / "apps" / "web-server"
            if (web_server / "server" / "routes" / "git.py").is_file():
                return web_server
    raise RuntimeError("Could not locate apps/web-server on any parent path")


_WEB_SERVER = _find_web_server_root()
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes.git import McpServerConfig, check_mcp_health  # noqa: E402

GIT_LOGGER = "server.routes.git"

# The exact envelope AC#5 pins for a refused URL. Held as constants so drift
# surfaces as one obvious diff rather than scattered per-assertion literals.
EXPECTED_STATUS = "unknown"
EXPECTED_MESSAGE = "Cannot check server"


def _addrinfo(*ips: str) -> list:
    """Build a ``getaddrinfo``-shaped result for the given IPv4 literals."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
        for ip in ips
    ]


def _call_health_refused(url: str, resolved_ips) -> dict:
    """Invoke ``check_mcp_health`` with the resolver stubbed to ``resolved_ips``.

    A link-local / metadata address makes the *real* guard raise, exercising the
    production refusal branch. ``urlopen`` is stubbed to explode so a regression
    that fell through the ``except`` and actually fetched the refused URL fails
    loudly instead of returning a plausible-looking response.
    """
    server = McpServerConfig(id="srv-1", name="probe", type="http", url=url)

    def _never_fetch(*args, **kwargs):  # pragma: no cover - asserted via failure
        raise AssertionError("a refused MCP URL must never be fetched")

    with patch(f"{GIT_LOGGER}.socket.getaddrinfo", return_value=_addrinfo(*resolved_ips)), \
            patch("urllib.request.urlopen", _never_fetch):
        return asyncio.run(check_mcp_health(server))


def test_check_mcp_health_refused_url_returns_unknown_envelope():
    """The full returned dict matches the pre-change "unknown" shape exactly.

    Asserted as one equality so an *added* field (e.g. a leaked ``reason``) is
    caught too — for a shape-rendering frontend an extra key is as much a
    contract break as a missing one.
    """
    result = _call_health_refused(
        "http://probe.internal/mcp", ["169.254.169.254"]
    )

    assert result == {
        "success": True,
        "data": {
            "serverId": "srv-1",
            "status": EXPECTED_STATUS,
            "message": EXPECTED_MESSAGE,
        },
    }


def test_check_mcp_health_refused_url_does_not_leak_reason_in_body():
    """Boundary: the concrete refusal reason never appears in the returned body.

    A link-local resolution makes the guard raise ``"Disallowed MCP server
    address"``; that detail (and the probed host) must stay in the log only, or a
    caller gains an SSRF oracle for internal network reachability.
    """
    result = _call_health_refused(
        "http://probe.internal/mcp", ["169.254.169.254"]
    )

    serialised = json.dumps(result)
    assert "Disallowed MCP server address" not in serialised
    assert "probe.internal" not in serialised


def test_check_mcp_health_refused_url_logs_the_reason(caplog):
    """The specific reason IS logged — the operator-facing half of AC#5."""
    caplog.set_level(logging.WARNING, logger=GIT_LOGGER)

    _call_health_refused("http://probe.internal/mcp", ["169.254.169.254"])

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert any("Disallowed MCP server address" in message for message in warnings), (
        f"expected the refusal reason in a WARNING record, got {warnings!r}"
    )
