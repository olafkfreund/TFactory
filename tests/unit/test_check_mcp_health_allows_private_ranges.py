# AC#3: Private ranges (10/8, 172.16/12, 192.168/16) remain allowed by the
# MCP SSRF guard.
#
# Proven through the PUBLIC route handler `check_mcp_health`, which calls the
# private `_is_safe_mcp_url` helper internally. Allowance is publicly
# observable: a private address falls through the guard, so the handler
# proceeds to the outbound HEAD request and returns the "healthy" shape
# instead of the "unknown" / "Cannot check server" refusal shape.
#
# A link-local negative control (169.254.10.10) and a mixed case
# (private first, link-local second) are included in this file so the
# positive assertions cannot pass by accident -- see AC#1 ("every resolved
# address is checked, not just the first").

import asyncio
import socket
from unittest.mock import MagicMock, patch

import pytest

from server.routes.git import McpServerConfig, check_mcp_health

# Hostname deliberately NOT one of the hard-coded metadata hostnames
# (169.254.169.254 / metadata.google.internal) so the pre-resolution
# hostname blocklist cannot be what produces the result.
TEST_HOST = "mcp.internal.example"
TEST_URL = f"http://{TEST_HOST}/mcp"

REFUSAL_MESSAGE = "Cannot check server"


def _addrinfo(*addresses):
    """Build a socket.getaddrinfo-shaped result for the given IPv4 addresses."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
        for address in addresses
    ]


def _server(server_id):
    return McpServerConfig(id=server_id, name="mcp-under-test", type="http", url=TEST_URL)


def _call_handler(addresses, server_id):
    """Drive check_mcp_health with DNS + urlopen mocked; return (response, urlopen_mock)."""
    urlopen_mock = MagicMock(return_value=MagicMock(status=200))
    with patch("server.routes.git.socket.getaddrinfo", return_value=_addrinfo(*addresses)):
        with patch("urllib.request.urlopen", urlopen_mock):
            response = asyncio.run(check_mcp_health(_server(server_id)))
    return response, urlopen_mock


@pytest.mark.parametrize(
    "private_address",
    ["10.1.2.3", "172.16.5.4", "192.168.1.10"],
    ids=["rfc1918-10-8", "rfc1918-172-16-12", "rfc1918-192-168-16"],
)
def test_check_mcp_health_private_address_is_allowed_past_ssrf_guard(private_address):
    """AC#3: an RFC1918 address is not refused -- the handler reaches the HEAD request."""
    response, urlopen_mock = _call_handler([private_address], server_id="srv-private")

    assert response == {
        "success": True,
        "data": {
            "serverId": "srv-private",
            "status": "healthy",
            "message": "Server responded",
        },
    }


@pytest.mark.parametrize(
    "private_address",
    ["10.1.2.3", "172.16.5.4", "192.168.1.10"],
    ids=["rfc1918-10-8", "rfc1918-172-16-12", "rfc1918-192-168-16"],
)
def test_check_mcp_health_private_address_issues_outbound_request(private_address):
    """AC#3: allowance is observable -- urlopen is called exactly once with the configured URL."""
    _, urlopen_mock = _call_handler([private_address], server_id="srv-private")

    assert urlopen_mock.call_count == 1
    request_arg = urlopen_mock.call_args.args[0]
    assert request_arg.full_url == TEST_URL


def test_check_mcp_health_link_local_address_returns_refusal_shape():
    """Negative control (AC#1/AC#5): link-local is refused with the 'unknown' shape."""
    response, urlopen_mock = _call_handler(["169.254.10.10"], server_id="srv-linklocal")

    assert response == {
        "success": True,
        "data": {
            "serverId": "srv-linklocal",
            "status": "unknown",
            "message": REFUSAL_MESSAGE,
        },
    }
    assert urlopen_mock.call_count == 0


def test_check_mcp_health_private_then_link_local_is_still_refused():
    """AC#1: 'private is allowed' must not short-circuit the remaining-address check."""
    response, urlopen_mock = _call_handler(
        ["10.1.2.3", "169.254.10.10"], server_id="srv-mixed"
    )

    assert response["data"]["status"] == "unknown"
    assert response["data"]["message"] == REFUSAL_MESSAGE
    assert urlopen_mock.call_count == 0
