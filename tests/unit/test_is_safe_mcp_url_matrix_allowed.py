"""AC#6 (allowed half of the matrix): ``_is_safe_mcp_url`` accepts
``http://localhost``, ``http://127.0.0.1``, a private ``10.x`` address and a
public host.

AC#6 names seven cases. The other tests in this spec pin the four that must be
*refused* (``169.254.169.254``, an IPv6 link-local literal, ``file://``, and the
unresolvable/fail-closed path). This file pins the remaining three-plus-one that
must be *accepted*, because refusal-only coverage is satisfied by a guard that
returns False for everything — an SSRF check that blocks the developer's own
loopback MCP server is a broken feature, not a safe one.

Each accept must reach ``return True`` through the branch the AC cares about:

    http://localhost  → resolves to 127.0.0.1 *and* ::1  → is_loopback  (AC#2)
    http://127.0.0.1  → literal loopback                 → is_loopback  (AC#2)
    http://10.0.0.5   → RFC1918                          → is_private   (AC#3)
    http://example.com→ globally routable                → fall-through

AC#2 is the subtle one and gets its own case here: IPv6 ``::1`` also satisfies
``is_reserved``, so ``localhost`` — which resolves to both families — only stays
allowed while the loopback test runs *before* the reserved test. A reordering
regression makes ``http://localhost`` behave differently by address family; the
dual-stack case below fails loudly when that happens.

The resolver is patched at the import site (``git.socket.getaddrinfo``) rather
than hitting DNS, so the test is hermetic — no network, and no dependence on how
the CI host happens to resolve ``localhost``.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest

from server.routes import git


LOOPBACK_V4 = "127.0.0.1"
LOOPBACK_V6 = "::1"
PRIVATE_10 = "10.0.0.5"
PRIVATE_172 = "172.16.4.9"
PRIVATE_192 = "192.168.1.20"
PUBLIC_V4 = "93.184.216.34"


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
    actually consulted.
    """

    def install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            fake_getaddrinfo.calls += 1
            fake_getaddrinfo.hosts.append(host)
            return _addrinfo(*addresses)

        fake_getaddrinfo.calls = 0
        fake_getaddrinfo.hosts = []
        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
        return fake_getaddrinfo

    return install


@pytest.mark.parametrize(
    "url,addresses",
    [
        ("http://localhost:8080/mcp", (LOOPBACK_V4, LOOPBACK_V6)),
        ("http://127.0.0.1:8080/mcp", (LOOPBACK_V4,)),
        ("http://10.0.0.5:8080/mcp", (PRIVATE_10,)),
        ("http://example.com/mcp", (PUBLIC_V4,)),
    ],
    ids=[
        "ac6-localhost-dual-stack",
        "ac6-127.0.0.1-literal",
        "ac6-private-10.x",
        "ac6-public-host",
    ],
)
def test_is_safe_mcp_url_allowed_matrix_returns_true(resolve_to, url, addresses):
    """AC#6: every URL in the allowed half of the matrix is accepted."""
    resolve_to(*addresses)

    assert git._is_safe_mcp_url(url) is True


def test_is_safe_mcp_url_localhost_allowed_for_each_address_family(resolve_to):
    """AC#2: ``http://localhost`` is accepted on IPv4 *and* IPv6 alone.

    Split out from the dual-stack case above because a loopback/reserved
    ordering regression only bites the ``::1``-resolving host: ``127.0.0.1``
    keeps passing while ``::1`` starts raising, so ``http://localhost`` silently
    "behaves differently by address family" exactly as the AC warns.
    """
    resolve_to(LOOPBACK_V4)
    assert git._is_safe_mcp_url("http://localhost:8080/mcp") is True

    resolve_to(LOOPBACK_V6)
    assert git._is_safe_mcp_url("http://localhost:8080/mcp") is True


def test_is_safe_mcp_url_ipv6_loopback_is_allowed_despite_being_reserved(resolve_to):
    """AC#2 mechanism: ``::1`` is reserved, and must still be allowed.

    This is the precondition that makes the ordering requirement real — if
    ``::1`` were not ``is_reserved``, branch order would not matter and the
    test above would prove nothing.
    """
    assert ipaddress.ip_address(LOOPBACK_V6).is_reserved is True

    resolve_to(LOOPBACK_V6)

    assert git._is_safe_mcp_url("http://localhost/mcp") is True


@pytest.mark.parametrize(
    "address",
    [PRIVATE_10, PRIVATE_172, PRIVATE_192],
    ids=["rfc1918-10/8", "rfc1918-172.16/12", "rfc1918-192.168/16"],
)
def test_is_safe_mcp_url_private_ranges_are_allowed(resolve_to, address):
    """AC#3: all three RFC1918 ranges remain allowed, not just 10/8."""
    resolve_to(address)

    assert git._is_safe_mcp_url(f"http://{address}:8080/mcp") is True


def test_is_safe_mcp_url_allowed_host_consults_the_resolver(resolve_to):
    """The accept rests on resolved-address evidence, not a hostname shortcut.

    If a named host is ever accepted with zero resolver calls, the guard has
    stopped checking what the name actually points at — which is the whole
    mechanism the change introduced.
    """
    fake = resolve_to(PUBLIC_V4)

    assert git._is_safe_mcp_url("http://example.com/mcp") is True
    assert fake.calls == 1
    assert fake.hosts == ["example.com"]


def test_is_safe_mcp_url_allowed_host_still_refused_with_link_local_sibling(
    resolve_to,
):
    """Negative control (AC#1): an allowed *name* is not a blanket pass.

    ``localhost`` answering with loopback plus ``169.254.169.254`` — the
    DNS-rebinding shape — must still be refused. Without this, the accepts
    above would be consistent with "the first address wins" rather than
    "every resolved address is checked".
    """
    resolve_to(LOOPBACK_V4, "169.254.169.254")

    with pytest.raises(git.HTTPException) as excinfo:
        git._is_safe_mcp_url("http://localhost:8080/mcp")

    assert excinfo.value.status_code == 400
