"""AC#5: ``check_mcp_health`` keeps its existing ``status: "unknown"`` response
shape when a URL is refused by the SSRF guard; the reason is logged, not
returned to the caller.

Target: apps/web-server/server/routes/git.py::check_mcp_health

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path. ``socket.getaddrinfo`` is patched at
the import site of the module under test, so no DNS or network is touched —
the refusal comes from the real ``_is_safe_mcp_url`` logic, not from mocking
the subject.
"""

import asyncio
import ipaddress
import logging
import socket

import pytest

from server.routes import git


SERVER_ID = "mcp-server-42"

# AC#6 fixtures: the metadata endpoint and an IPv6 link-local literal.
IMDS_HOST_URL = "http://169.254.169.254/latest/meta-data/"
IPV4_LINK_LOCAL = "169.254.169.254"
IPV6_LINK_LOCAL = "fe80::1"
MULTICAST = "224.0.0.1"


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
    """Patch getaddrinfo at the import site of the module under test."""

    def _install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return _addrinfo(*addresses)

        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)

    return _install


@pytest.fixture
def resolve_fails(monkeypatch):
    """Make hostname resolution fail, exercising the fail-closed path (AC#4)."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def no_network(monkeypatch):
    """Explode if the endpoint ever attempts the outbound HEAD request."""
    import urllib.request

    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("urlopen must not be called for a refused URL")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    return calls


def _check(url):
    """Drive the async route function with a minimal http MCP server config."""
    config = git.McpServerConfig(
        id=SERVER_ID,
        name="metadata-probe",
        type="http",
        url=url,
    )
    return asyncio.run(git.check_mcp_health(config))


EXPECTED_REFUSED_RESPONSE = {
    "success": True,
    "data": {
        "serverId": SERVER_ID,
        "status": "unknown",
        "message": "Cannot check server",
    },
}


def test_check_mcp_health_refused_metadata_url_returns_unknown_shape(no_network):
    """A blocked metadata URL yields the unchanged unknown-status envelope."""
    assert _check(IMDS_HOST_URL) == EXPECTED_REFUSED_RESPONSE


@pytest.mark.parametrize(
    "address",
    [IPV4_LINK_LOCAL, IPV6_LINK_LOCAL, MULTICAST],
    ids=["ipv4-link-local", "ipv6-link-local", "multicast"],
)
def test_check_mcp_health_refused_address_echoes_server_id(
    resolve_to, no_network, address
):
    """Whatever the refusal reason, serverId is echoed and status is unknown."""
    resolve_to(address)

    result = _check("http://blocked.example.test:3000/")

    assert result == EXPECTED_REFUSED_RESPONSE


def test_check_mcp_health_unresolvable_host_returns_unknown_shape(
    resolve_fails, no_network
):
    """AC#4 fail-closed: an unresolvable host also gets the unknown envelope."""
    assert _check("http://nonexistent.example.test:3000/") == EXPECTED_REFUSED_RESPONSE


def test_check_mcp_health_refusal_reason_is_not_returned_to_the_client(
    resolve_to, no_network
):
    """The HTTPException detail must never leak into the response body."""
    resolve_to(IPV4_LINK_LOCAL)

    result = _check("http://blocked.example.test:3000/")

    body = result["data"]
    assert body["message"] == "Cannot check server"
    assert "Disallowed" not in body["message"]
    assert sorted(body) == ["message", "serverId", "status"]


def test_check_mcp_health_refusal_reason_is_logged(resolve_to, no_network, caplog):
    """The reason is logged (not returned), per AC#5."""
    resolve_to(IPV4_LINK_LOCAL)

    with caplog.at_level(logging.WARNING, logger=git.logger.name):
        _check("http://blocked.example.test:3000/")

    assert any(
        "SSRF validation failed" in record.getMessage() for record in caplog.records
    )


def test_check_mcp_health_allowed_loopback_url_is_not_short_circuited(resolve_to):
    """Guard against over-blocking: loopback still reaches the HEAD request path.

    The outbound call is stubbed to fail, so the endpoint reports ``unhealthy``
    rather than ``unknown`` — proving the SSRF guard did not refuse the URL.
    """
    import urllib.request

    resolve_to("127.0.0.1")

    def failing_urlopen(*args, **kwargs):
        raise OSError("connection refused")

    original = urllib.request.urlopen
    urllib.request.urlopen = failing_urlopen
    try:
        result = _check("http://localhost:3103/mcp")
    finally:
        urllib.request.urlopen = original

    assert result["data"]["status"] == "unhealthy"
