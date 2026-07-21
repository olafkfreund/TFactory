"""AC#1: ``_is_safe_mcp_url`` resolves the URL host and rejects any address in a
link-local, reserved, multicast or unspecified range — and every resolved
address is checked, not just the first.

This file covers the *post-resolution* rejection branch specifically: the host
itself is unremarkable (it is not on the hostname blocklist and the scheme is
valid), so the only thing that can refuse it is the per-address range check.
That is the branch an SSRF bypass would slip through — a DNS name that resolves
to 169.254.169.254, fe80::1, or 0.0.0.0 looks harmless until it is resolved.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


# A host that is NOT on the pre-resolution blocklist, so the refusal has to come
# from the resolved address rather than from the hostname string.
BENIGN_HOST_URL = "http://mcp.internal.example.com:3103/mcp"

SAFE_PUBLIC_ADDR = "203.0.113.42"

# (address, why it must be refused) — one representative per disallowed range.
DISALLOWED_ADDRESSES = [
    ("169.254.1.1", "ipv4-link-local"),
    ("169.254.169.254", "ipv4-link-local-metadata"),
    ("fe80::1", "ipv6-link-local"),
    ("224.0.0.1", "ipv4-multicast"),
    ("ff02::1", "ipv6-multicast"),
    ("0.0.0.0", "ipv4-unspecified"),
    ("::", "ipv6-unspecified"),
    ("4000::1", "ipv6-reserved"),
]

DISALLOWED_IDS = [reason for _, reason in DISALLOWED_ADDRESSES]
DISALLOWED_ONLY = [addr for addr, _ in DISALLOWED_ADDRESSES]


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
    """Patch getaddrinfo at the import site so resolution is deterministic.

    Returns an installer taking the IP strings the fake resolver should hand
    back for any hostname.
    """

    def _install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            fake_getaddrinfo.calls += 1
            return _addrinfo(*addresses)

        fake_getaddrinfo.calls = 0
        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
        return fake_getaddrinfo

    return _install


@pytest.mark.parametrize("address", DISALLOWED_ONLY, ids=DISALLOWED_IDS)
def test_is_safe_mcp_url_disallowed_range_raises_http_400(resolve_to, address):
    """AC#1: a host resolving into a disallowed range raises HTTPException 400."""
    resolve_to(address)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("address", DISALLOWED_ONLY, ids=DISALLOWED_IDS)
def test_is_safe_mcp_url_disallowed_range_never_returns(resolve_to, address):
    """Fail-closed proof: a disallowed address must raise, never return truthy.

    Returning ``False`` instead of raising would silently pass the SSRF guard in
    ``check_mcp_health``, which only catches ``HTTPException``.
    """
    resolve_to(address)

    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url(BENIGN_HOST_URL)
        pytest.fail(f"expected HTTPException, got return value {result!r}")


@pytest.mark.parametrize("address", DISALLOWED_ONLY, ids=DISALLOWED_IDS)
def test_is_safe_mcp_url_disallowed_range_blamed_on_the_address(resolve_to, address):
    """The refusal is attributed to the address, not to a malformed URL.

    Pins the branch: if a regression started rejecting these as "invalid URL"
    (scheme/hostname branch) the range check would be dead code.
    """
    resolve_to(address)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert "address" in str(exc_info.value.detail).lower()


@pytest.mark.parametrize("address", DISALLOWED_ONLY, ids=DISALLOWED_IDS)
def test_is_safe_mcp_url_rejects_disallowed_address_in_last_position(
    resolve_to, address
):
    """AC#1 boundary: EVERY resolved address is checked, not just the first.

    The resolver returns a safe public address first, so a guard that inspected
    only ``results[0]`` would accept the URL and expose the disallowed host.
    """
    resolve_to(SAFE_PUBLIC_ADDR, address)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("address", DISALLOWED_ONLY, ids=DISALLOWED_IDS)
def test_disallowed_addresses_are_actually_out_of_range_precondition(address):
    """Precondition: each fixture address really is in a disallowed range.

    Without this the parametrized rejections above could pass vacuously if one
    of the literals were mistyped into a merely-public address.
    """
    ip = ipaddress.ip_address(address)

    assert ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved


def test_is_safe_mcp_url_accepts_public_address_control(resolve_to):
    """Control: the same benign host resolving to a public address is accepted.

    Proves the rejections above come from the range check rather than from the
    fixture URL being rejected for some unrelated reason.
    """
    resolve_to(SAFE_PUBLIC_ADDR)

    assert git._is_safe_mcp_url(BENIGN_HOST_URL) is True
