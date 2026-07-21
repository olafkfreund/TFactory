"""AC#3: Private ranges (10/8, 172.16/12, 192.168/16) remain allowed.

The SSRF guard added in this change refuses link-local, reserved, multicast and
unspecified addresses. AC#3 is the *non*-regression half of that: a self-hosted
MCP server on the operator's own LAN — the overwhelmingly common deployment —
must keep working. A guard written as "reject anything that isn't public" would
satisfy every rejection criterion in this spec and still break every real user,
so the allowance needs its own pinned test.

This file proves the three RFC-1918 blocks named in the criterion are accepted,
that the acceptance survives the block boundaries (first and last usable
address of each), that it is earned by resolving rather than by pattern-matching
the URL, and — the boundary that keeps AC#3 from swallowing AC#1 —
that 169.254.169.254 is NOT rescued by the private allowance even though the
stdlib classifies it as private too.

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


# The three addresses the acceptance criterion names verbatim.
PRIVATE_10 = "10.0.0.5"
PRIVATE_172 = "172.16.0.5"
PRIVATE_192 = "192.168.1.5"

AC3_ADDRESSES = [PRIVATE_10, PRIVATE_172, PRIVATE_192]
AC3_IDS = ["10-slash-8", "172-16-slash-12", "192-168-slash-16"]

# A hostname with no special-casing anywhere in the helper, so every verdict
# below has to come from the resolved address rather than from the URL string.
LAN_URL = "http://mcp.internal.example:8080/sse"

# First and last usable address of each RFC-1918 block. If the guard ever grew
# a narrower notion of "private" (say, only 192.168.0.0/24), the midpoints above
# could still pass while real deployments at the edges broke.
BOUNDARY_ADDRESSES = [
    "10.0.0.0",
    "10.255.255.255",
    "172.16.0.0",
    "172.31.255.255",
    "192.168.0.0",
    "192.168.255.255",
]
BOUNDARY_IDS = [
    "10-block-first",
    "10-block-last",
    "172-block-first",
    "172-block-last",
    "192-block-first",
    "192-block-last",
]

# The metadata address. ``ipaddress`` reports it as BOTH private and link-local,
# which is precisely why the helper must test link-local before the private
# allowance — see the boundary test at the bottom of this file.
METADATA_ADDRESS = "169.254.169.254"


def _addrinfo(*addresses):
    """Build a ``socket.getaddrinfo``-shaped 5-tuple list for the given IPs.

    Order is preserved exactly as given: the helper walks every record, so which
    address comes back first is load-bearing for the multi-record cases.
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
    order, for any hostname. No real DNS is consulted, so the suite is hermetic
    and cannot flake on a resolver that hijacks unknown names. The fake records
    its calls so a test can prove resolution actually happened.
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


@pytest.mark.parametrize("address", AC3_ADDRESSES, ids=AC3_IDS)
def test_ac3_addresses_are_private_precondition(address):
    """Precondition: the three named addresses really are RFC-1918.

    Without this, a stdlib change could silently turn the acceptance assertions
    below into vacuous "a public address is allowed" checks.
    """
    assert ipaddress.ip_address(address).is_private is True


@pytest.mark.parametrize("address", AC3_ADDRESSES, ids=AC3_IDS)
def test_is_safe_mcp_url_allows_private_address(resolve_to, address):
    """AC#3 core: each named private address is accepted on its own."""
    resolve_to(address)

    assert git._is_safe_mcp_url(LAN_URL) is True


def test_is_safe_mcp_url_allows_all_three_private_ranges_identically(resolve_to):
    """AC#3 identity: no private block is treated differently from the others.

    The per-address tests above would each fail loudly on a total regression,
    but a guard that allowed only 10/8 (the easiest block to hard-code) would
    produce a mixed result set here — one accepted range and two refusals —
    which is the shape this assertion is built to catch.
    """
    outcomes = set()
    for address in AC3_ADDRESSES:
        resolve_to(address)
        try:
            outcomes.add(("returned", git._is_safe_mcp_url(LAN_URL)))
        except HTTPException as exc:  # pragma: no cover - only on a regression
            outcomes.add(("raised", exc.status_code, exc.detail))

    assert outcomes == {("returned", True)}


@pytest.mark.parametrize("address", BOUNDARY_ADDRESSES, ids=BOUNDARY_IDS)
def test_is_safe_mcp_url_allows_private_block_boundaries(resolve_to, address):
    """Boundary: the first and last address of each RFC-1918 block is allowed.

    Off-by-one range arithmetic fails here while the comfortable midpoints in
    the core test keep passing.
    """
    resolve_to(address)

    assert git._is_safe_mcp_url(LAN_URL) is True


def test_is_safe_mcp_url_allows_host_resolving_to_several_private_addresses(
    resolve_to,
):
    """A multi-homed LAN host resolving into all three blocks is still allowed.

    The helper checks *every* record (AC#1), so acceptance must hold across the
    whole answer, not just the first entry.
    """
    resolve_to(PRIVATE_10, PRIVATE_172, PRIVATE_192)

    assert git._is_safe_mcp_url(LAN_URL) is True


def test_is_safe_mcp_url_private_acceptance_consults_the_resolver(resolve_to):
    """Acceptance is earned by resolving the host, not by reading the URL.

    A guard that allowlisted names looking internal would never call
    ``getaddrinfo``, would pass every assertion above, and would wave through an
    attacker-controlled hostname pointing anywhere at all.
    """
    resolver = resolve_to(PRIVATE_10)

    assert git._is_safe_mcp_url(LAN_URL) is True
    assert resolver.calls == 1
    assert resolver.hosts == ["mcp.internal.example"]


def test_is_safe_mcp_url_private_allowance_does_not_rescue_metadata_address(
    resolve_to,
):
    """Boundary between AC#3 and AC#1: private-is-allowed must not out-rank
    link-local-is-refused.

    ``169.254.169.254`` reports ``is_private is True`` in the stdlib. A guard
    that returned early on ``is_private`` before testing ``is_link_local`` would
    pass every other test in this file and hand the cloud metadata endpoint
    straight back to the caller — the exact defect this spec exists to close.
    """
    assert ipaddress.ip_address(METADATA_ADDRESS).is_private is True

    resolve_to(METADATA_ADDRESS)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(LAN_URL)

    assert exc_info.value.status_code == 400


def test_is_safe_mcp_url_private_address_beside_link_local_is_refused(resolve_to):
    """The private allowance is a ``continue``, not a ``return True``.

    A legitimate-looking private record first, the metadata address second: if
    the helper exited on the first acceptable address it would never reach the
    dangerous one. This is the smuggling path AC#3 must not open.
    """
    resolve_to(PRIVATE_192, METADATA_ADDRESS)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(LAN_URL)

    assert exc_info.value.status_code == 400
