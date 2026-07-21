"""AC#2: loopback is allowed, and it is checked *before* any reserved test --
IPv6 ``::1`` also satisfies ``is_reserved``, so the order of the two checks
decides whether ``http://localhost`` behaves the same on both address families.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

Why order is the whole test: in the stdlib, ``ipaddress.ip_address("::1")`` is
BOTH ``is_loopback`` and ``is_reserved`` (``::/8`` is a reserved block). A guard
written as "reject if is_reserved, else allow loopback" therefore refuses
``http://localhost`` on a dual-stack machine (where it resolves to ``::1``)
while accepting it on an IPv4-only one -- a family-dependent bug. These tests
pin ``socket.getaddrinfo`` so the resolved family is fixed, and assert the IPv6
loopback is accepted. The control at the bottom proves the reserved check still
exists, so the acceptance is not a blanket "reserved is fine".
"""

from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


def _web_server_root() -> Path:
    """Locate the ``apps/web-server`` package root without hard-coding a path.

    Walks the ancestors of this file and of the runner's cwd looking for the
    directory that actually contains ``server/routes/git.py``.
    """
    candidates: list[Path] = []
    for start in (Path(__file__).resolve(), Path.cwd().resolve() / "_"):
        for parent in start.parents:
            candidates.append(parent)
            candidates.append(parent / "apps" / "web-server")
    for candidate in candidates:
        if (candidate / "server" / "routes" / "git.py").is_file():
            return candidate
    raise RuntimeError(
        "Could not locate apps/web-server (server/routes/git.py) from "
        f"{Path(__file__).resolve()} or {Path.cwd().resolve()}"
    )


_WEB_SERVER = _web_server_root()
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes.git import _is_safe_mcp_url  # noqa: E402

# ``localhost`` is not on the pre-resolution deny list, so the verdict can only
# come from the addresses the fixture supplies -- which is the point.
LOCALHOST_URL = "http://localhost:3103/sse"

LOOPBACK_V6 = "::1"
LOOPBACK_V4 = "127.0.0.1"
# Reserved (4000::/3) but NOT loopback, private, link-local, multicast or
# unspecified -- so only the reserved test can refuse it.
RESERVED_NOT_LOOPBACK_V6 = "4000::1"


def _addrinfo(*addresses: str) -> list[tuple]:
    """Build a ``getaddrinfo``-shaped result list for the given IP literals."""
    infos = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
        infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return infos


@pytest.fixture
def resolves_to(monkeypatch):
    """Pin ``socket.getaddrinfo`` (as used by git.py) to an ordered address list."""

    def _install(*addresses: str) -> None:
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return _addrinfo(*addresses)

        monkeypatch.setattr("server.routes.git.socket.getaddrinfo", fake_getaddrinfo)

    return _install


def test_ipv6_loopback_is_also_reserved_so_check_order_matters():
    """Pins the stdlib premise this AC rests on: ``::1`` is loopback AND reserved.

    If a future Python ever stopped classifying ``::1`` as reserved, the
    ordering requirement below would become vacuous -- this test says so loudly
    instead of letting the real tests silently stop proving anything.
    """
    loopback_v6 = ipaddress.ip_address(LOOPBACK_V6)

    assert loopback_v6.is_loopback is True
    assert loopback_v6.is_reserved is True


def test_is_safe_mcp_url_ipv6_loopback_only_host_is_accepted(resolves_to):
    """AC#2: a host resolving solely to ``::1`` is safe, despite being reserved.

    This is the failing case for any implementation that tests ``is_reserved``
    before ``is_loopback``: such a guard raises HTTPException(400) here.
    """
    resolves_to(LOOPBACK_V6)

    assert _is_safe_mcp_url(LOCALHOST_URL) is True


@pytest.mark.parametrize(
    "addresses",
    [
        (LOOPBACK_V6,),
        (LOOPBACK_V4,),
        (LOOPBACK_V4, LOOPBACK_V6),
        (LOOPBACK_V6, LOOPBACK_V4),
    ],
    ids=[
        "ipv6-only",
        "ipv4-only",
        "dual-stack-v4-first",
        "dual-stack-v6-first",
    ],
)
def test_is_safe_mcp_url_localhost_is_accepted_on_every_address_family(
    resolves_to, addresses
):
    """AC#2: ``http://localhost`` must not behave differently by address family."""
    resolves_to(*addresses)

    assert _is_safe_mcp_url(LOCALHOST_URL) is True


def test_is_safe_mcp_url_ipv6_loopback_literal_url_is_accepted(resolves_to):
    """AC#2: the ``http://[::1]`` literal form is accepted too, not just the name."""
    resolves_to(LOOPBACK_V6)

    assert _is_safe_mcp_url("http://[::1]:3103/sse") is True


def test_is_safe_mcp_url_reserved_non_loopback_ipv6_is_still_refused(resolves_to):
    """Control: the reserved check survives -- ``::1`` is an exception, not a hole.

    Without this, an implementation that simply deleted the ``is_reserved``
    branch would pass every test above for the wrong reason.
    """
    resolves_to(RESERVED_NOT_LOOPBACK_V6)

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(LOCALHOST_URL)

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_loopback_does_not_whitelist_the_rest_of_the_host(
    resolves_to,
):
    """AC#1 + AC#2: an accepted loopback entry must not short-circuit the scan.

    ``::1`` is allowed, but the reserved address that follows it still refuses
    the URL -- loopback means "this address is fine", not "this host is fine".
    """
    resolves_to(LOOPBACK_V6, RESERVED_NOT_LOOPBACK_V6)

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(LOCALHOST_URL)

    assert excinfo.value.status_code == 400
