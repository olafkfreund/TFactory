"""AC#1: every resolved address is checked, not just the first.

This file pins the *iteration* half of AC#1. Its sibling
(``test_is_safe_mcp_url_rejects_disallowed_ranges.py``) proves that each
disallowed range is refused; this one proves the guard cannot be walked past by
putting a harmless address in front of the dangerous one.

That is the realistic SSRF shape: an attacker controls a DNS name and publishes
two A/AAAA records — a public one and ``169.254.169.254`` (or ``fe80::1``). A
guard written as ``ip = ipaddress.ip_address(results[0][4][0])`` accepts that
name, and the health check then dials whichever address the OS resolver happens
to pick. Only a loop over *all* of ``socket.getaddrinfo``'s results closes it.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, matching the sibling test file so
the pre-flight import check resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


# A host that is NOT on the pre-resolution hostname blocklist, so any refusal
# has to come from the per-address range check rather than the URL string.
BENIGN_HOST_URL = "http://mcp.internal.example.com:3103/mcp"

# Documentation-range addresses (RFC 5737 / RFC 3849): globally routable as far
# as ``ipaddress`` is concerned, so they are accepted and act as safe padding.
SAFE_PUBLIC_V4 = "203.0.113.42"
SAFE_PUBLIC_V4_ALT = "198.51.100.7"
SAFE_PUBLIC_V6 = "2001:db8::1"

# Loopback and private are explicitly allowed (AC#2 / AC#3) — used as padding
# to prove the loop's ``continue`` branches do not terminate the scan early.
SAFE_ALLOWED_PADDING = ["127.0.0.1", "::1", "10.0.0.5", "192.168.1.10"]

# The address that must be caught no matter where it sits in the result list.
DANGEROUS_ADDRESSES = [
    ("169.254.169.254", "cloud-metadata"),
    ("169.254.1.1", "ipv4-link-local"),
    ("fe80::1", "ipv6-link-local"),
    ("224.0.0.1", "ipv4-multicast"),
    ("ff02::1", "ipv6-multicast"),
    ("0.0.0.0", "ipv4-unspecified"),
]

DANGEROUS_ONLY = [addr for addr, _ in DANGEROUS_ADDRESSES]
DANGEROUS_IDS = [reason for _, reason in DANGEROUS_ADDRESSES]


def _addrinfo(*addresses):
    """Build a ``socket.getaddrinfo``-shaped 5-tuple list for the given IPs.

    Order is preserved exactly as given — that ordering is the whole point of
    this file, so it must never be normalised or sorted.
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
    can prove resolution actually happened.
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


@pytest.mark.parametrize("dangerous", DANGEROUS_ONLY, ids=DANGEROUS_IDS)
def test_is_safe_mcp_url_rejects_when_safe_address_resolves_first(
    resolve_to, dangerous
):
    """AC#1 core: safe address first, dangerous second — still refused.

    This is the exact subtask scenario. A first-address-only guard returns True
    here; the loop-over-all guard raises 400.
    """
    resolve_to(SAFE_PUBLIC_V4, dangerous)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("dangerous", DANGEROUS_ONLY, ids=DANGEROUS_IDS)
def test_is_safe_mcp_url_blames_the_address_when_it_resolves_second(
    resolve_to, dangerous
):
    """The 400 is attributed to the address, not to a malformed/unresolvable URL.

    Without this, a regression that started raising "Could not resolve" for
    multi-address results would still satisfy the status-code assertion above
    while having stopped checking ranges entirely.
    """
    resolve_to(SAFE_PUBLIC_V4, dangerous)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert "address" in str(exc_info.value.detail).lower()


@pytest.mark.parametrize("position", [0, 1, 2, 3], ids=["first", "second", "third", "last"])
def test_is_safe_mcp_url_rejects_link_local_at_any_position(resolve_to, position):
    """AC#1 boundary: position in the result list must not matter.

    The dangerous address is slid through every slot of a 4-address result. A
    guard that inspects only ``results[0]`` passes the ``first`` case and fails
    the other three; a guard that breaks out of the loop early fails the tail.
    """
    addresses = [SAFE_PUBLIC_V4, SAFE_PUBLIC_V4_ALT, SAFE_PUBLIC_V6, "10.0.0.5"]
    addresses[position] = "169.254.169.254"
    resolve_to(*addresses)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert exc_info.value.status_code == 400


def test_is_safe_mcp_url_rejects_link_local_behind_allowed_continue_branches(
    resolve_to,
):
    """The loop's ``continue`` branches must not end the scan.

    Loopback (AC#2) and private (AC#3) addresses are accepted via ``continue``.
    If either were written as an early ``return True`` instead, this URL — whose
    dangerous address sits *after* all of them — would be wrongly accepted.
    """
    resolve_to(*SAFE_ALLOWED_PADDING, "fe80::1")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(BENIGN_HOST_URL)

    assert exc_info.value.status_code == 400


def test_is_safe_mcp_url_rejects_when_only_the_final_address_is_dangerous(
    resolve_to,
):
    """A single bad address among many safe ones is enough to refuse the URL.

    Pins "reject if ANY is unsafe" rather than a majority/first/last heuristic.
    """
    resolve_to(
        SAFE_PUBLIC_V4,
        SAFE_PUBLIC_V4_ALT,
        SAFE_PUBLIC_V6,
        SAFE_PUBLIC_V4,
        "169.254.169.254",
    )

    with pytest.raises(HTTPException):
        git._is_safe_mcp_url(BENIGN_HOST_URL)


def test_is_safe_mcp_url_accepts_multiple_safe_addresses_control(resolve_to):
    """Control: many safe addresses, none dangerous — accepted.

    Proves the rejections above are caused by the injected dangerous address and
    not merely by the result list having more than one entry.
    """
    resolver = resolve_to(*SAFE_ALLOWED_PADDING, SAFE_PUBLIC_V4, SAFE_PUBLIC_V6)

    assert git._is_safe_mcp_url(BENIGN_HOST_URL) is True
    assert resolver.calls == 1


@pytest.mark.parametrize("dangerous", DANGEROUS_ONLY, ids=DANGEROUS_IDS)
def test_dangerous_addresses_are_actually_disallowed_precondition(dangerous):
    """Precondition: each fixture address really is in a disallowed range.

    Without this the parametrized rejections could pass vacuously if a literal
    were mistyped into a merely-public address.
    """
    ip = ipaddress.ip_address(dangerous)

    assert ip.is_link_local or ip.is_multicast or ip.is_unspecified


@pytest.mark.parametrize(
    "safe",
    [SAFE_PUBLIC_V4, SAFE_PUBLIC_V4_ALT, SAFE_PUBLIC_V6, *SAFE_ALLOWED_PADDING],
    ids=["v4-doc", "v4-doc-alt", "v6-doc", "v4-loopback", "v6-loopback", "private-10", "private-192"],
)
def test_padding_addresses_are_actually_allowed_precondition(safe):
    """Precondition: the padding addresses are ones the guard must accept.

    If a padding literal were itself disallowed, the ordering tests would raise
    for the wrong reason and prove nothing about later addresses being checked.
    """
    ip = ipaddress.ip_address(safe)

    assert not (ip.is_link_local or ip.is_multicast or ip.is_unspecified)
    assert ip.is_loopback or ip.is_private or not ip.is_reserved
