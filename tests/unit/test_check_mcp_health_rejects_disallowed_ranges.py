"""AC#1: the URL host is resolved and any address in a link-local, reserved,
multicast or unspecified range is rejected — and *every* resolved address is
checked, not just the first.

Target: apps/web-server/server/routes/git.py::check_mcp_health

This exercises AC#1 through the public route handler rather than the private
``_is_safe_mcp_url`` helper (which is not importable by name in the pre-flight
environment). Only public names are imported: ``check_mcp_health`` and
``McpServerConfig``.

``socket.getaddrinfo`` is patched on the stdlib ``socket`` module — the exact
object the module under test resolves through (``git.py`` does ``import
socket`` and calls ``socket.getaddrinfo``) — so no DNS is touched and the
refusal comes from the real guard logic, not from mocking the subject.
``urllib.request.urlopen`` is patched to explode, proving a refused URL never
produces an outbound request.
"""

import asyncio
import ipaddress
import socket
import urllib.request

import pytest

from server.routes.git import McpServerConfig, check_mcp_health


SERVER_ID = "mcp-server-ac1"
PROBE_URL = "http://mcp.example.test:3000/mcp"

# AC#1 disallowed ranges, one representative address each.
IPV4_LINK_LOCAL = "169.254.169.254"
IPV6_LINK_LOCAL = "fe80::1"
MULTICAST = "224.0.0.1"
UNSPECIFIED = "0.0.0.0"
# 4000::/3 is reserved and is neither private, link-local nor multicast, so it
# can only be refused by the reserved check itself.
IPV6_RESERVED = "4000::1"

# Control: a public, globally routable address that AC#1 must NOT refuse.
PUBLIC_ADDRESS = "93.184.216.34"

EXPECTED_REFUSED_RESPONSE = {
    "success": True,
    "data": {
        "serverId": SERVER_ID,
        "status": "unknown",
        "message": "Cannot check server",
    },
}


def _addrinfo(*addresses):
    """Build a getaddrinfo-shaped result list for the given IP strings."""
    entries = []
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.version == 6:
            entries.append(
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, 0, 0, 0))
            )
        else:
            entries.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)))
    return entries


@pytest.fixture
def resolve_to(monkeypatch):
    """Make host resolution in the module under test return fixed addresses."""

    def _install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return _addrinfo(*addresses)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    return _install


@pytest.fixture
def urlopen_calls(monkeypatch):
    """Record every outbound urllib request; the stub never touches the network."""
    calls = []

    def fake_urlopen(req, *args, **kwargs):
        calls.append(req)
        return object()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _check(url=PROBE_URL):
    """Drive the async route handler with a minimal http MCP server config."""
    config = McpServerConfig(
        id=SERVER_ID,
        name="disallowed-range-probe",
        type="http",
        url=url,
    )
    return asyncio.run(check_mcp_health(config))


@pytest.mark.parametrize(
    "address",
    [IPV4_LINK_LOCAL, IPV6_LINK_LOCAL, MULTICAST, UNSPECIFIED, IPV6_RESERVED],
    ids=[
        "ipv4-link-local",
        "ipv6-link-local",
        "multicast",
        "unspecified",
        "ipv6-reserved",
    ],
)
def test_check_mcp_health_disallowed_address_returns_unknown_envelope(
    resolve_to, urlopen_calls, address
):
    """A host resolving into a disallowed range yields the unknown envelope."""
    resolve_to(address)

    assert _check() == EXPECTED_REFUSED_RESPONSE


@pytest.mark.parametrize(
    "address",
    [IPV4_LINK_LOCAL, IPV6_LINK_LOCAL, MULTICAST, UNSPECIFIED, IPV6_RESERVED],
    ids=[
        "ipv4-link-local",
        "ipv6-link-local",
        "multicast",
        "unspecified",
        "ipv6-reserved",
    ],
)
def test_check_mcp_health_disallowed_address_makes_no_outbound_request(
    resolve_to, urlopen_calls, address
):
    """The refusal short-circuits before any urllib request is issued."""
    resolve_to(address)

    _check()

    assert urlopen_calls == []


def test_check_mcp_health_rejects_when_only_a_later_address_is_disallowed(
    resolve_to, urlopen_calls
):
    """AC#1 boundary: every resolved address is checked, not just the first."""
    resolve_to(PUBLIC_ADDRESS, IPV4_LINK_LOCAL)

    result = _check()

    assert result == EXPECTED_REFUSED_RESPONSE
    assert urlopen_calls == []


def test_check_mcp_health_public_address_is_not_refused_by_the_range_guard(
    resolve_to, urlopen_calls
):
    """Control: a globally routable address passes the guard and is probed.

    Without this, every assertion above would still pass on a handler that
    refused unconditionally.
    """
    resolve_to(PUBLIC_ADDRESS)

    result = _check()

    assert result["data"]["status"] == "healthy"
    assert len(urlopen_calls) == 1
