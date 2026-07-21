# AC#1: A helper resolves the URL host and rejects any address in a link-local,
# reserved, multicast or unspecified range. EVERY resolved address is checked,
# not just the first.
#
# Exercised through the public route handler `check_mcp_health`, which calls the
# private `_is_safe_mcp_url` guard internally. A hostname that resolves to two
# addresses — one safe public, one link-local — must be refused regardless of the
# order the resolver returns them in. The both-public control proves the test is
# not simply passing for every input.

import asyncio
import socket
from unittest.mock import patch

import pytest

from server.routes.git import McpServerConfig, check_mcp_health

# A hostname deliberately NOT in the hard-coded metadata blocklist
# ("169.254.169.254" / "metadata.google.internal") so the refusal can only come
# from the per-address resolution loop.
TEST_HOSTNAME = "mcp.example.com"
TEST_URL = f"http://{TEST_HOSTNAME}:8080"

PUBLIC_IP = "93.184.216.34"
ANOTHER_PUBLIC_IP = "93.184.216.35"
LINK_LOCAL_IP = "169.254.10.10"

REFUSED_RESPONSE_DATA = {
    "serverId": "srv-multi-addr",
    "status": "unknown",
    "message": "Cannot check server",
}


def _addrinfo(*ips):
    """Build a getaddrinfo-shaped result list for the given IPv4 addresses."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 8080))
        for ip in ips
    ]


@pytest.fixture
def http_server():
    """An http-type MCP server config pointing at the multi-address test host."""
    return McpServerConfig(
        id="srv-multi-addr",
        name="multi-address mcp",
        type="http",
        url=TEST_URL,
    )


@pytest.mark.parametrize(
    "resolved_ips",
    [
        (PUBLIC_IP, LINK_LOCAL_IP),
        (LINK_LOCAL_IP, PUBLIC_IP),
    ],
    ids=["safe-address-first", "link-local-address-first"],
)
def test_check_mcp_health_with_any_link_local_address_returns_unknown_without_request(
    http_server, resolved_ips
):
    """A link-local address anywhere in the resolution list refuses the check."""
    with patch(
        "server.routes.git.socket.getaddrinfo", return_value=_addrinfo(*resolved_ips)
    ), patch("urllib.request.urlopen") as mock_urlopen:
        response = asyncio.run(check_mcp_health(http_server))

    assert response == {"success": True, "data": REFUSED_RESPONSE_DATA}
    assert mock_urlopen.call_count == 0


def test_check_mcp_health_with_all_public_addresses_attempts_outbound_request(
    http_server,
):
    """Control: every resolved address public → the guard passes and the check runs."""
    with patch(
        "server.routes.git.socket.getaddrinfo",
        return_value=_addrinfo(PUBLIC_IP, ANOTHER_PUBLIC_IP),
    ), patch("urllib.request.urlopen") as mock_urlopen:
        response = asyncio.run(check_mcp_health(http_server))

    assert mock_urlopen.call_count == 1
    assert response["data"]["status"] != "unknown"
    assert response["data"]["message"] != "Cannot check server"
