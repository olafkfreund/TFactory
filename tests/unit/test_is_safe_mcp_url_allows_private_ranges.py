"""AC#3: Private ranges (10/8, 172.16/12, 192.168/16) remain allowed.

The SSRF guard added in this change resolves the URL host and rejects
link-local / reserved / multicast / unspecified addresses. RFC 1918 space is
explicitly *not* part of that rejection: MCP servers legitimately run on the
LAN, in a Docker bridge network, or on a k8s pod IP, so tightening the guard
must not silently break every self-hosted deployment.

This file pins the allowance and — just as importantly — its boundary:

* ``10.0.0.5``, ``172.16.0.1`` and ``192.168.1.10`` are accepted, and
* ``169.254.169.254`` is still rejected even though ``ipaddress`` reports it
  as ``is_private`` too,

so a naive "allow anything private" rewrite that hoisted the private check
above the link-local check would turn a test red rather than reopen the
cloud-metadata hole.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, matching the sibling test files in
this spec.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


PRIVATE_10 = "10.0.0.5"
PRIVATE_172 = "172.16.0.1"
PRIVATE_192 = "192.168.1.10"

# Both link-local *and* private per ``ipaddress`` — the reason check order
# inside the loop is load-bearing.
METADATA_ADDRESS = "169.254.169.254"

# A hostname that is not on the pre-resolution blocklist, so the verdict has to
# come from the per-address range check rather than from the URL string.
MCP_URL = "http://mcp.internal.example:8080/sse"


def _addrinfo(*addresses):
    """Build a ``socket.getaddrinfo``-shaped 5-tuple list for the given IPs.

    Order is preserved exactly as given; several tests below depend on which
    record the resolver hands back first, so it must never be sorted.
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


@pytest.mark.parametrize(
    "address",
    [PRIVATE_10, PRIVATE_172, PRIVATE_192],
    ids=["rfc1918-10/8", "rfc1918-172.16/12", "rfc1918-192.168/16"],
)
def test_is_safe_mcp_url_private_address_returns_true(resolve_to, address):
    """AC#3 core: each of the three RFC 1918 blocks is accepted.

    These are the exact addresses named in the acceptance criterion.
    """
    resolve_to(address)

    assert git._is_safe_mcp_url(MCP_URL) is True


@pytest.mark.parametrize(
    "address",
    ["10.0.0.0", "10.255.255.255", "172.16.0.0", "172.31.255.255",
     "192.168.0.0", "192.168.255.255"],
    ids=["10/8-first", "10/8-last", "172.16/12-first", "172.16/12-last",
         "192.168/16-first", "192.168/16-last"],
)
def test_is_safe_mcp_url_private_range_edges_return_true(resolve_to, address):
    """Boundary: the first and last address of each private block is allowed.

    Catches an off-by-one in a hand-rolled prefix comparison (e.g. treating
    ``172.31.255.255`` as outside 172.16/12).
    """
    resolve_to(address)

    assert git._is_safe_mcp_url(MCP_URL) is True


def test_is_safe_mcp_url_all_private_addresses_together_return_true(resolve_to):
    """A multi-record answer made entirely of private addresses is accepted.

    Proves the loop's ``continue`` path runs to completion and returns True
    rather than falling through to a rejection after the first record.
    """
    resolve_to(PRIVATE_10, PRIVATE_172, PRIVATE_192)

    assert git._is_safe_mcp_url(MCP_URL) is True


def test_is_safe_mcp_url_private_acceptance_consults_the_resolver(resolve_to):
    """Acceptance is earned by resolving the host, not by string-matching it.

    A guard that allowlisted names that merely *looked* internal would never
    call ``getaddrinfo``.
    """
    resolver = resolve_to(PRIVATE_10)

    assert git._is_safe_mcp_url(MCP_URL) is True
    assert resolver.calls == 1


def test_metadata_address_is_also_private_precondition():
    """Precondition for the guard below: ``169.254.169.254`` reports as private.

    Without this, the next test would look like a redundant link-local case
    instead of the trap that it is — the private allowance and the link-local
    rejection genuinely overlap.
    """
    ip = ipaddress.ip_address(METADATA_ADDRESS)

    assert ip.is_private is True
    assert ip.is_link_local is True


def test_is_safe_mcp_url_private_allowance_does_not_admit_link_local(resolve_to):
    """The private allowance must not swallow the cloud-metadata endpoint.

    ``169.254.169.254`` satisfies ``is_private``, so an implementation that
    checked ``is_private`` before ``is_link_local`` would accept it. AC#3 must
    not be bought at the cost of AC#1.
    """
    resolve_to(METADATA_ADDRESS)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(MCP_URL)

    assert exc_info.value.status_code == 400


def test_is_safe_mcp_url_private_first_then_link_local_still_rejects(resolve_to):
    """A private first record must not short-circuit the remaining addresses.

    This is the AC#3/AC#1 interaction: an attacker-controlled name that
    resolves to a harmless ``10.x`` *and* to the metadata address must be
    refused, so the private branch has to ``continue``, not ``return True``.
    """
    resolve_to(PRIVATE_10, METADATA_ADDRESS)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(MCP_URL)

    assert exc_info.value.status_code == 400
