"""AC#2: Loopback is allowed, and it is checked BEFORE any reserved test —
IPv6 ``::1`` also satisfies ``is_reserved``, so order matters or
``http://localhost`` behaves differently by address family.

This file pins the *identity* half of that criterion: the answer for
``http://localhost`` must be the same value whether the resolver hands back
``127.0.0.1``, ``::1``, or both records in either order. A guard that tested
``is_reserved`` before ``is_loopback`` would accept the IPv4 record and refuse
the IPv6 one, making the same URL succeed or fail depending on which family the
resolver happened to return first — exactly the flaky, host-dependent behaviour
AC#2 forbids.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The helper is reached through its module (``from server.routes import git``)
rather than imported by name, matching the sibling test files of this spec so
the pre-flight import check resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


IPV4_LOOPBACK = "127.0.0.1"
IPV6_LOOPBACK = "::1"

# A hostname that is NOT on the pre-resolution blocklist, so the answer has to
# come from the per-address range check rather than from the URL string itself.
LOCALHOST_URL = "http://localhost:3103/mcp"

# Every way "localhost" can plausibly resolve on a real machine.
FAMILY_CASES = [
    (IPV4_LOOPBACK,),
    (IPV6_LOOPBACK,),
    (IPV4_LOOPBACK, IPV6_LOOPBACK),
    (IPV6_LOOPBACK, IPV4_LOOPBACK),
]
FAMILY_IDS = ["ipv4-only", "ipv6-only", "dual-stack-v4-first", "dual-stack-v6-first"]


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


def _outcome(url):
    """Collapse the helper's two exit shapes into one comparable token.

    ``_is_safe_mcp_url`` signals refusal by raising ``HTTPException`` and
    acceptance by returning ``True``. To assert that two address families are
    handled *identically* we need a single value that captures either outcome.
    """
    try:
        return ("returned", git._is_safe_mcp_url(url))
    except HTTPException as exc:  # pragma: no cover - only on a regression
        return ("raised", exc.status_code, exc.detail)


def test_ipv6_loopback_is_reserved_precondition():
    """Precondition for AC#2: ``::1`` really does satisfy ``is_reserved``.

    If the stdlib ever stopped classifying ``::1`` as reserved, the ordering
    assertions below would still pass but would prove nothing.
    """
    assert ipaddress.ip_address(IPV6_LOOPBACK).is_reserved is True


def test_ipv4_loopback_is_not_reserved_precondition():
    """Precondition: ``127.0.0.1`` is NOT reserved — only the v6 case is at risk.

    This asymmetry is why the defect is address-family dependent, and why an
    IPv4-only test would have shipped it.
    """
    assert ipaddress.ip_address(IPV4_LOOPBACK).is_reserved is False


@pytest.mark.parametrize("addresses", FAMILY_CASES, ids=FAMILY_IDS)
def test_is_safe_mcp_url_localhost_accepted_for_every_family(resolve_to, addresses):
    """AC#2 core: ``http://localhost`` is accepted for each resolution shape."""
    resolve_to(*addresses)

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_localhost_outcome_identical_across_families(resolve_to):
    """AC#2 identity: every family produces the *same* outcome token.

    This is the assertion the ordering bug fails on. With ``is_reserved`` tested
    before ``is_loopback`` the IPv4 case yields ``("returned", True)`` while the
    IPv6 and v6-first cases yield ``("raised", 400, ...)`` — a set of size two
    instead of one, i.e. "http://localhost behaves differently by address
    family".
    """
    outcomes = set()
    for addresses in FAMILY_CASES:
        resolve_to(*addresses)
        outcomes.add(_outcome(LOCALHOST_URL))

    assert outcomes == {("returned", True)}


def test_is_safe_mcp_url_ipv6_loopback_allowed_despite_being_reserved(resolve_to):
    """The ordering proof in isolation: ``::1`` is reserved yet still accepted."""
    resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_localhost_acceptance_consults_the_resolver(resolve_to):
    """Acceptance is earned by resolving, not by string-matching "localhost".

    A guard that allowlisted the literal hostname would never call
    ``getaddrinfo``, would trivially pass the cases above, and would happily
    accept an attacker-controlled name that merely *looked* local.
    """
    resolver = resolve_to(IPV6_LOOPBACK)

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True
    assert resolver.calls == 1
    assert resolver.hosts == ["localhost"]


def test_is_safe_mcp_url_dual_stack_localhost_does_not_shortcut_later_address(
    resolve_to,
):
    """Boundary: the loopback allowance is a ``continue``, not ``return True``.

    Loopback first, link-local second — accepting on the first record would let
    the metadata address through behind a harmless ``127.0.0.1``, so "identical
    across families" must not be bought by exiting early.
    """
    resolve_to(IPV4_LOOPBACK, "169.254.169.254")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(LOCALHOST_URL)

    assert exc_info.value.status_code == 400
