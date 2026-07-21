"""AC#5: the refusal reason is logged, not returned.

When `_is_safe_mcp_url()` refuses an MCP server URL, `check_mcp_health` must
surface the concrete reason (the HTTPException detail, plus the offending URL)
in a logger WARNING record only. The payload handed back to the client keeps
the generic `status: "unknown"` shape and must NOT leak the reason or the host.

Pure unit test — hostname resolution is mocked, so no network is touched.
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
    """Locate `apps/web-server` by walking up from this file and the cwd."""
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


def _addrinfo(*ips: str) -> list:
    """Build a getaddrinfo-shaped result for the given IPv4 literals."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
        for ip in ips
    ]


REFUSAL_CASES = [
    # (url, getaddrinfo side effect / return value, reason expected in the log)
    (
        "http://169.254.169.254/mcp",
        _addrinfo("169.254.169.254"),
        "Disallowed MCP server URL",
    ),
    (
        "http://cloud-metadata.example/mcp",
        _addrinfo("169.254.169.254"),
        "Disallowed MCP server address",
    ),
    (
        "http://no-such-host.invalid/mcp",
        socket.gaierror("Name or service not known"),
        "Could not resolve MCP server hostname",
    ),
]

REFUSAL_IDS = [
    "metadata-ip-blocklisted-by-hostname",
    "resolves-to-link-local",
    "unresolvable-host-fails-closed",
]


def _call_health(url: str, resolver_behaviour) -> dict:
    """Invoke check_mcp_health with socket.getaddrinfo stubbed out."""
    server = McpServerConfig(id="srv-1", name="probe", type="http", url=url)
    kwargs = (
        {"side_effect": resolver_behaviour}
        if isinstance(resolver_behaviour, Exception)
        else {"return_value": resolver_behaviour}
    )
    with patch(f"{GIT_LOGGER}.socket.getaddrinfo", **kwargs):
        return asyncio.run(check_mcp_health(server))


@pytest.mark.parametrize(
    "url,resolver_behaviour,reason",
    REFUSAL_CASES,
    ids=REFUSAL_IDS,
)
def test_check_mcp_health_refusal_reason_is_logged(
    caplog, url, resolver_behaviour, reason
):
    """The concrete refusal reason reaches the WARNING log."""
    caplog.set_level(logging.WARNING, logger=GIT_LOGGER)

    _call_health(url, resolver_behaviour)

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert any(reason in message for message in warnings), (
        f"expected refusal reason {reason!r} in a WARNING record, got {warnings!r}"
    )


@pytest.mark.parametrize(
    "url,resolver_behaviour,reason",
    REFUSAL_CASES,
    ids=REFUSAL_IDS,
)
def test_check_mcp_health_refusal_reason_is_not_returned(
    caplog, url, resolver_behaviour, reason
):
    """The reason (and the offending URL) never appear in the returned payload."""
    caplog.set_level(logging.WARNING, logger=GIT_LOGGER)

    payload = _call_health(url, resolver_behaviour)

    serialised = json.dumps(payload)
    assert reason not in serialised
    assert url not in serialised
    assert payload["data"]["status"] == "unknown"
    assert payload["data"]["message"] == "Cannot check server"


def test_check_mcp_health_logs_the_offending_url_but_omits_it_from_the_payload(
    caplog,
):
    """Boundary: the operator-facing log keeps the URL the client is not shown."""
    caplog.set_level(logging.WARNING, logger=GIT_LOGGER)
    url = "http://cloud-metadata.example/mcp"

    payload = _call_health(url, _addrinfo("10.0.0.5", "169.254.169.254"))

    assert url in caplog.text
    assert "cloud-metadata.example" not in json.dumps(payload)
