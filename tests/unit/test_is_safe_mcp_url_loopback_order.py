"""AC#2: Loopback is allowed by _is_safe_mcp_url, and it is checked BEFORE any
`is_reserved` test — IPv6 ``::1`` also satisfies ``is_reserved``, so the order of
the checks matters, otherwise ``http://localhost`` would behave differently
depending on which address family it resolved to.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest

from server.routes import git


IPV4_LOOPBACK = "127.0.0.1"
IPV6_LOOPBACK = "::1"


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


def test_ipv6_loopback_is_reserved_precondition():
    """Precondition for AC#2: ``::1`` really does satisfy ``is_reserved``.

    If this ever stops being true the ordering assertion below is vacuous.
    """
    assert ipaddress.ip_address(IPV6_LOOPBACK).is_reserved is True


@pytest.mark.parametrize(
    "addresses",
    [
        (IPV4_LOOPBACK,),
        (IPV6_LOOPBACK,),
        (IPV4_LOOPBACK, IPV6_LOOPBACK),
        (IPV6_LOOPBACK, IPV4_LOOPBACK),
    ],
    ids=["ipv4-only", "ipv6-only", "ipv4-then-ipv6", "ipv6-then-ipv4"],
)
def test_is_safe_mcp_url_localhost_loopback_returns_true(resolve_to, addresses):
    """Loopback hosts are accepted regardless of address family or ordering."""
    resolve_to(*addresses)

    assert git._is_safe_mcp_url("http://localhost:3103") is True


def test_is_safe_mcp_url_ipv6_loopback_allowed_despite_being_reserved(resolve_to):
    """The AC#2 ordering proof: ``::1`` is reserved yet still accepted.

    A reserved-before-loopback implementation would raise HTTPException here.
    """
    resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url("http://localhost:3103") is True


def test_is_safe_mcp_url_ipv6_loopback_literal_host_allowed(resolve_to):
    """An explicit ``[::1]`` literal host is accepted the same way."""
    resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url("http://[::1]:3103/mcp") is True
