"""AC#6: The allowed half of the URL matrix — ``_is_safe_mcp_url`` must return
True for ``http://localhost``, ``http://127.0.0.1``, a private 10.x address and
a public host.

This is the counterpart to ``test_is_safe_mcp_url_matrix_refused.py``. Where
that file pins *why* each bad URL is refused, this one pins that the guard is
not merely paranoid: the four legitimate destinations an operator would
actually point an MCP server at all survive validation, each via a different
branch of the address check:

* ``http://localhost``   — resolves to loopback (both families); loopback branch
* ``http://127.0.0.1``   — IPv4 loopback literal; loopback branch
* ``http://10.0.0.5``    — RFC1918 10/8; private branch (AC#3)
* ``http://example.com`` — globally routable address; falls through to ``return True``

A regression that over-blocks (e.g. moving the ``is_reserved`` test above the
loopback test, so IPv6 ``::1`` is rejected) fails here rather than silently
breaking every local MCP server.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import ipaddress
import socket

from server.routes import git


LOCALHOST_URL = "http://localhost:3103/mcp"
LOOPBACK_V4_URL = "http://127.0.0.1:3103/mcp"
PRIVATE_10_URL = "http://10.0.0.5:8080/mcp"
PUBLIC_URL = "http://example.com/mcp"

# example.com — globally routable, neither private nor reserved under
# ipaddress's iana-special-registry view. Asserted as a precondition below.
PUBLIC_ADDRESS = "93.184.216.34"


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


def _install_resolver(monkeypatch, *addresses):
    """Patch getaddrinfo at the import site to return a fixed address set.

    Returns the fake, which exposes ``.calls`` so a test can assert the
    resolver really was consulted (i.e. the URL got past the scheme/hostname
    gate and was accepted on the *address* evidence, not by short-circuit).
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        fake_getaddrinfo.calls += 1
        return _addrinfo(*addresses)

    fake_getaddrinfo.calls = 0
    monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
    return fake_getaddrinfo


def test_is_safe_mcp_url_localhost_hostname_is_allowed(monkeypatch):
    """AC#6: ``http://localhost`` is safe when it resolves to loopback."""
    _install_resolver(monkeypatch, "127.0.0.1")

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_loopback_literal_is_allowed(monkeypatch):
    """AC#6: the ``127.0.0.1`` literal is safe."""
    _install_resolver(monkeypatch, "127.0.0.1")

    assert git._is_safe_mcp_url(LOOPBACK_V4_URL) is True


def test_is_safe_mcp_url_private_10_slash_8_address_is_allowed(monkeypatch):
    """AC#3/AC#6: an RFC1918 10/8 address stays allowed (LAN MCP servers)."""
    _install_resolver(monkeypatch, "10.0.0.5")

    assert git._is_safe_mcp_url(PRIVATE_10_URL) is True


def test_is_safe_mcp_url_public_host_is_allowed(monkeypatch):
    """AC#6: a globally routable host is safe — the guard is not an allowlist."""
    _install_resolver(monkeypatch, PUBLIC_ADDRESS)

    assert git._is_safe_mcp_url(PUBLIC_URL) is True


def test_is_safe_mcp_url_localhost_dual_stack_is_allowed(monkeypatch):
    """AC#2/AC#6: localhost resolving to BOTH ``::1`` and ``127.0.0.1`` is safe.

    The boundary case the loopback-ordering rule exists for: IPv6 ``::1`` also
    satisfies ``is_reserved``, so a guard that tested reserved-ness first would
    reject dual-stack localhost while accepting the IPv4-only form.
    """
    _install_resolver(monkeypatch, "::1", "127.0.0.1")

    assert git._is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_allowed_url_consults_the_resolver(monkeypatch):
    """A public host is accepted on resolved-address evidence, not by short-circuit.

    If this ever passes with zero resolver calls, acceptance stopped depending
    on what the hostname actually resolves to — which is the whole guard.
    """
    fake = _install_resolver(monkeypatch, PUBLIC_ADDRESS)

    git._is_safe_mcp_url(PUBLIC_URL)

    assert fake.calls == 1


def test_public_address_is_neither_private_nor_reserved_precondition():
    """Precondition: the "public" fixture address really is public.

    Python's ``is_private`` covers the IANA special-registry ranges (including
    the TEST-NET blocks), so a documentation address would make the public-host
    test vacuously pass through the private branch instead.
    """
    ip = ipaddress.ip_address(PUBLIC_ADDRESS)

    assert (ip.is_private, ip.is_reserved, ip.is_loopback) == (False, False, False)


def test_ipv6_loopback_is_also_reserved_precondition():
    """Precondition for the dual-stack test: ``::1`` is loopback AND reserved.

    This is what makes check-order observable; if it stops holding, the
    dual-stack test no longer proves anything about ordering.
    """
    ip = ipaddress.ip_address("::1")

    assert (ip.is_loopback, ip.is_reserved) == (True, True)
