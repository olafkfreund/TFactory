"""AC#6: Tests cover ``169.254.169.254``, an IPv6 link-local literal,
``file://``, ``http://localhost``, ``http://127.0.0.1``, a private 10.x address
and a public host.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

This file owns the **refused** half of that matrix: the cloud-metadata IPv4
literal, an IPv6 link-local literal, and a non-HTTP ``file://`` URL. Each must
be rejected, and each is rejected for a *different* reason inside the guard --
the pre-resolution metadata denylist, the per-address link-local test, and the
scheme check respectively -- so the three cases are asserted separately rather
than collapsed into one blanket "raises" assertion.

The allowed half of the matrix (localhost, 127.0.0.1, 10.x, a public host) is
covered by the loopback / private-range subtasks.
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


def _addrinfo(*addresses: str) -> list[tuple]:
    """Build a getaddrinfo-shaped result list for the given IP literals."""
    infos = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
        infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return infos


@pytest.fixture
def resolves_literally(monkeypatch):
    """Pin ``socket.getaddrinfo`` to echo the requested host back as its address.

    Keeps the test hermetic (no DNS, no IPv6-capable host required) while
    preserving the behaviour of an IP literal: it "resolves" to itself.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return _addrinfo(host)

    monkeypatch.setattr("server.routes.git.socket.getaddrinfo", fake_getaddrinfo)


def test_is_safe_mcp_url_cloud_metadata_literal_is_refused(resolves_literally):
    """AC#6: ``http://169.254.169.254`` -- the cloud metadata endpoint -- is refused."""
    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url("http://169.254.169.254/latest/meta-data/")

    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://[fe80::1]/sse",
        "https://[fe80::abcd:1234]:8080/sse",
    ],
    ids=["ipv6-link-local-bare", "ipv6-link-local-with-port"],
)
def test_is_safe_mcp_url_ipv6_link_local_literal_is_refused(resolves_literally, url):
    """AC#6: an IPv6 link-local literal is refused regardless of port/scheme."""
    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(url)

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_file_scheme_is_refused(resolves_literally):
    """AC#6: a ``file://`` URL is refused -- only http/https are acceptable."""
    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url("file:///etc/passwd")

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_refused_matrix_never_returns_true(resolves_literally):
    """None of the AC#6 refused cases may fall through to a ``True`` verdict.

    Guards the failure mode where a future refactor turns a raise into a
    silently-permissive return value.
    """
    refused = [
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/sse",
        "file:///etc/passwd",
    ]

    for url in refused:
        with pytest.raises(HTTPException):
            assert _is_safe_mcp_url(url) is not True
