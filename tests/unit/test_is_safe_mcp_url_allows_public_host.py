"""AC#6: ``_is_safe_mcp_url`` returns True for a host that resolves to an
ordinary public address such as ``93.184.216.34``.

This is the "the guard is not an allowlist" half of the matrix. Every other
test for this change pins something the guard must *refuse* (link-local,
metadata, multicast, unresolvable); refusal-only coverage is satisfied by a
helper that returns False for everything. This file pins the opposite edge:
a globally routable destination must reach ``return True``, and it must do so
by falling through *every* branch of the address loop —

    is_loopback → no   (not 127/8, not ::1)
    is_link_local / is_multicast / is_unspecified → no
    is_private → no    (not RFC1918)
    is_reserved → no
    ⇒ return True

so a regression that widens any branch (e.g. testing ``is_reserved`` with a
too-broad predicate, or reordering it above the fall-through) breaks here.

The resolver is patched at the import site (``git.socket.getaddrinfo``) rather
than hitting DNS, so the test is hermetic and deterministic — no network, no
dependence on whether ``example.com`` still answers.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest

from server.routes import git


PUBLIC_URL = "http://example.com/mcp"

# The AC's named address. Also exercised: a second unrelated public IPv4 and a
# public IPv6, so the test doesn't hinge on one literal.
PUBLIC_IPV4 = "93.184.216.34"
PUBLIC_IPV4_ALT = "8.8.8.8"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"

# A link-local sibling used for the negative control below.
LINK_LOCAL = "169.254.169.254"


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
    """Patch ``getaddrinfo`` at the import site to return a fixed address set.

    Yields a callable ``resolve_to(*addresses)`` that returns the installed
    fake, which exposes ``.calls`` so a test can assert the resolver was
    actually consulted (i.e. the verdict rests on resolved-address evidence,
    not on a hostname short-circuit).
    """

    def install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            fake_getaddrinfo.calls += 1
            return _addrinfo(*addresses)

        fake_getaddrinfo.calls = 0
        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
        return fake_getaddrinfo

    return install


@pytest.mark.parametrize(
    "address",
    [PUBLIC_IPV4, PUBLIC_IPV4_ALT, PUBLIC_IPV6],
    ids=["ac-named-93.184.216.34", "public-ipv4-8.8.8.8", "public-ipv6"],
)
def test_is_safe_mcp_url_public_address_returns_true(resolve_to, address):
    """AC#6: a host resolving to an ordinary public address is accepted."""
    resolve_to(address)

    assert git._is_safe_mcp_url(PUBLIC_URL) is True


def test_is_safe_mcp_url_public_host_consults_the_resolver(resolve_to):
    """Acceptance rests on the resolved address, not on the hostname.

    If this ever passes with zero resolver calls, the guard stopped checking
    what the name actually points at — which is the entire mechanism.
    """
    fake = resolve_to(PUBLIC_IPV4)

    assert git._is_safe_mcp_url(PUBLIC_URL) is True
    assert fake.calls == 1


def test_is_safe_mcp_url_multiple_public_addresses_all_accepted(resolve_to):
    """AC#1/AC#6: a multi-homed public host is accepted on every address.

    The every-address rule cuts both ways: it must not reject a host merely
    for having more than one A/AAAA record.
    """
    resolve_to(PUBLIC_IPV4, PUBLIC_IPV4_ALT, PUBLIC_IPV6)

    assert git._is_safe_mcp_url(PUBLIC_URL) is True


def test_is_safe_mcp_url_public_host_with_link_local_sibling_is_refused(resolve_to):
    """Negative control (AC#1): "public" is not a blanket pass.

    A DNS-rebinding-flavoured answer that pairs a real public address with
    ``169.254.169.254`` must still be refused — otherwise the accept above
    would be proving "the first address wins", not "every address is checked".
    """
    resolve_to(PUBLIC_IPV4, LINK_LOCAL)

    with pytest.raises(git.HTTPException) as excinfo:
        git._is_safe_mcp_url(PUBLIC_URL)

    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "address",
    [PUBLIC_IPV4, PUBLIC_IPV4_ALT, PUBLIC_IPV6],
    ids=["ac-named-93.184.216.34", "public-ipv4-8.8.8.8", "public-ipv6"],
)
def test_public_fixture_addresses_are_genuinely_public_precondition(address):
    """Precondition: the fixture addresses reach the fall-through branch.

    Python's ``is_private`` covers the IANA special-registry ranges (TEST-NET,
    documentation prefixes, …). If one of these literals were special, the
    accept above would pass vacuously via the *private* branch and prove
    nothing about public hosts.
    """
    ip = ipaddress.ip_address(address)

    assert (
        ip.is_private,
        ip.is_reserved,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_unspecified,
    ) == (False, False, False, False, False, False)
