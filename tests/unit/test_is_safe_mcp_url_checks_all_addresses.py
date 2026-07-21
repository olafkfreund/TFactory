"""AC#1 (every-address clause): a host resolving to a safe public address
first and a link-local address second is still refused -- proving the guard
grades *every* resolved address, not just the first one.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

A dual-stack host is the realistic SSRF bypass here: an attacker-controlled
name answers with a harmless public A record and a link-local AAAA record
(or a second A record pointing at 169.254.169.254). A guard that inspects
only ``getaddrinfo(...)[0]`` accepts that name and the subsequent request can
land on cloud metadata. These tests pin ``socket.getaddrinfo`` to a fixed,
ordered address list so the unsafe entry sits at a known position, then
assert the URL is refused regardless of where it sits.
"""

from __future__ import annotations

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

# A benign hostname: it is not on the pre-resolution deny list, so the verdict
# can only come from the resolved addresses the fixture supplies.
BENIGN_URL = "http://mcp.vendor.example.com:8080/sse"

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
LINK_LOCAL_V6 = "fe80::1"
METADATA_V4 = "169.254.169.254"


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
    """Pin ``socket.getaddrinfo`` (as used by git.py) to an ordered address list.

    Returns the installer; the installer returns a mutable call-log list so a
    test can assert the guard resolved exactly once.
    """

    def _install(*addresses: str) -> list[str]:
        calls: list[str] = []

        def fake_getaddrinfo(host, port, *args, **kwargs):
            calls.append(host)
            return _addrinfo(*addresses)

        monkeypatch.setattr("server.routes.git.socket.getaddrinfo", fake_getaddrinfo)
        return calls

    return _install


def test_is_safe_mcp_url_public_first_link_local_second_is_refused(resolves_to):
    """AC#1: a safe leading address must not shadow an unsafe trailing one."""
    resolves_to(PUBLIC_V4, LINK_LOCAL_V6)

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(BENIGN_URL)

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_public_first_metadata_second_is_refused(resolves_to):
    """AC#1: the cloud-metadata address is caught even behind a public A record."""
    resolves_to(PUBLIC_V4, METADATA_V4)

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(BENIGN_URL)

    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "addresses",
    [
        (LINK_LOCAL_V6, PUBLIC_V4),
        (PUBLIC_V4, LINK_LOCAL_V6),
        (PUBLIC_V4, PUBLIC_V6, LINK_LOCAL_V6),
        (PUBLIC_V4, "10.0.0.5", "127.0.0.1", METADATA_V4),
    ],
    ids=[
        "unsafe-first",
        "unsafe-last-of-two",
        "unsafe-last-of-three",
        "unsafe-after-public-private-and-loopback",
    ],
)
def test_is_safe_mcp_url_refuses_regardless_of_unsafe_address_position(
    resolves_to, addresses
):
    """AC#1: position of the unsafe address in the resolution list is irrelevant."""
    resolves_to(*addresses)

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(BENIGN_URL)

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_all_safe_addresses_are_accepted(resolves_to):
    """Control: a multi-address host whose every entry is safe still passes.

    Without this, a guard that refused *any* multi-address host would pass the
    refusal tests above for the wrong reason.
    """
    resolves_to(PUBLIC_V4, PUBLIC_V6, "10.0.0.5", "127.0.0.1")

    assert _is_safe_mcp_url(BENIGN_URL) is True


def test_is_safe_mcp_url_resolves_the_url_hostname_exactly_once(resolves_to):
    """The guard grades one resolution result; it does not re-resolve per address."""
    calls = resolves_to(PUBLIC_V4, PUBLIC_V6)

    assert _is_safe_mcp_url(BENIGN_URL) is True
    assert calls == ["mcp.vendor.example.com"]
