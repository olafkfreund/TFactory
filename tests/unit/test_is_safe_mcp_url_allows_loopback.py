"""AC#2: Loopback is allowed, and it is checked BEFORE any reserved test.

IPv6 ``::1`` also satisfies ``ipaddress.IPv6Address.is_reserved``. A guard that
tests ``is_reserved`` before ``is_loopback`` therefore refuses ``http://localhost``
whenever the resolver hands back the IPv6 record first, and accepts it when the
IPv4 record wins the race — the same URL behaving differently by address family.

This file pins both halves of that claim:

* a host resolving to ``127.0.0.1`` is accepted, and
* a host resolving to ``::1`` is accepted *too*, despite being reserved,

so the ordering of the checks inside the loop is load-bearing and cannot be
swapped without turning a test red.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, matching the sibling test files in
this spec so the pre-flight import check resolves against a public attribute
path.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


IPV4_LOOPBACK = "127.0.0.1"
IPV4_LOOPBACK_ALT = "127.0.0.53"
IPV6_LOOPBACK = "::1"

# A hostname that is NOT on the pre-resolution blocklist, so acceptance has to
# come from the per-address range check rather than from the URL string.
LOCALHOST_URL = "http://localhost:3103/mcp"


def _addrinfo(*addresses):
    """Build a ``socket.getaddrinfo``-shaped 5-tuple list for the given IPs.

    Order is preserved exactly as given — which record comes back first is the
    whole point of this file, so it must never be normalised or sorted.
    """
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
    """Patch ``getaddrinfo`` at the import site so resolution is deterministic.

    Returns an installer taking the IP strings the fake resolver hands back, in
    order, for any hostname. The installed fake records its call count so a test
    can prove resolution actually happened rather than being short-circuited.
    """

    def _install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            fake_getaddrinfo.calls += 1
            fake_getaddrinfo.hosts.append(host)
            return _addrinfo(*addresses)

        fake_getaddrinfo.calls = 0
        fake_getaddrinfo.hosts = []
        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
        return fake_getaddrinfo

    return _install


def test_ipv6_loopback_is_reserved_precondition():
    """Precondition for AC#2: ``::1`` really does satisfy ``is_reserved``.

    If the stdlib ever stopped classifying ``::1`` as reserved, the ordering
    assertions below would still pass but would prove nothing.
    """
    assert ipaddress.ip_address(IPV6_LOOPBACK).is_reserved is True


def test_ipv4_loopback_is_not_reserved_precondition():
    """Precondition: ``127.0.0.1`` is NOT reserved — only the v6 case is at risk.

    This is why the bug is address-family dependent, and why an IPv4-only test
    would have shipped the defect.
    """
    assert ipaddress.ip_address(IPV4_LOOPBACK).is_reserved is False


@pytest.mark.parametrize(
    "addresses",
    [
        (IPV4_LOOPBACK,),
        (IPV4_LOOPBACK_ALT,),
        (IPV6_LOOPBACK,),
        (IPV4_LOOPBACK, IPV6_LOOPBACK),
        (IPV6_LOOPBACK, IPV4_LOOPBACK),
    ],
    ids=[
        "ipv4-127.0.0.1",
        "ipv4-127.0.0.53",
        "ipv6-::1",
        "dual-stack-ipv4-first",
        "dual-stack-ipv6-first",
    ],
)
def test_is_safe_mcp_url_loopback_host_returns_true(resolve_to, addresses):
    """AC#2 core: a loopback host is accepted for either address family.

    The dual-stack cases pin family-order independence: ``http://localhost``
    must not flip its answer based on which record the resolver returns first.
    """
    resolve_to(*addresses)

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_ipv6_loopback_allowed_despite_being_reserved(resolve_to):
    """The ordering proof: ``::1`` is reserved yet still accepted.

    An implementation that ran its ``is_reserved`` rejection before the loopback
    allowance would raise ``HTTPException`` here instead of returning True.
    """
    resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_ipv6_loopback_literal_host_allowed(resolve_to):
    """Boundary: an explicit bracketed ``[::1]`` literal host is accepted too.

    Covers the URL-parsing path where the host arrives already as an IPv6
    literal rather than as a name the resolver expands.
    """
    resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url("http://[::1]:3103/mcp") is True


def test_is_safe_mcp_url_ipv4_loopback_literal_host_allowed(resolve_to):
    """Boundary: the ``http://127.0.0.1`` literal from AC#6 is accepted."""
    resolve_to(IPV4_LOOPBACK)

    assert git._is_safe_mcp_url("http://127.0.0.1:11434/api") is True


def test_is_safe_mcp_url_loopback_acceptance_consults_the_resolver(resolve_to):
    """Acceptance is earned by resolving, not by string-matching "localhost".

    A guard that allowlisted the literal hostname would never call
    ``getaddrinfo`` and would happily accept an attacker-controlled name that
    merely *looked* local.
    """
    resolver = resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True
    assert resolver.calls == 1


def test_is_safe_mcp_url_loopback_does_not_shortcut_a_later_dangerous_address(
    resolve_to,
):
    """The loopback allowance is a ``continue``, not an early ``return True``.

    Loopback first, link-local second: accepting on the first record would let
    the metadata address through behind a harmless ``127.0.0.1``.
    """
    resolve_to(IPV4_LOOPBACK, "169.254.169.254")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(LOCALHOST_URL)

    assert exc_info.value.status_code == 400
