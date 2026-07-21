"""AC#4: an unresolvable host is treated as unsafe (fail closed).

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

Name resolution is the step that turns an attacker-supplied hostname into the
address the guard actually grades. If resolution fails -- ``socket.gaierror``,
an empty answer list, or any other resolver error -- there is no address to
grade, so the guard must refuse (HTTPException 400) rather than fall through
to ``return True`` or leak a bare resolver exception to the caller. These
tests pin ``socket.getaddrinfo`` (as imported by git.py) to each failure shape
and assert the refusal.
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

# A benign hostname: it is not on the pre-resolution deny list, so the only way
# this URL can be refused is via the resolution failure the fixture injects.
UNRESOLVABLE_URL = "http://no-such-host.invalid:8080/sse"


@pytest.fixture
def resolution_fails(monkeypatch):
    """Pin ``socket.getaddrinfo`` (as used by git.py) to raise a given error.

    Returns the installer; the installer returns a mutable call-log list so a
    test can assert the guard really attempted a resolution.
    """

    def _install(error: BaseException) -> list[str]:
        calls: list[str] = []

        def fake_getaddrinfo(host, port, *args, **kwargs):
            calls.append(host)
            raise error

        monkeypatch.setattr("server.routes.git.socket.getaddrinfo", fake_getaddrinfo)
        return calls

    return _install


@pytest.fixture
def resolution_returns_nothing(monkeypatch):
    """Pin ``socket.getaddrinfo`` to return an empty answer list."""

    def _install(empty=()) -> list[str]:
        calls: list[str] = []

        def fake_getaddrinfo(host, port, *args, **kwargs):
            calls.append(host)
            return list(empty)

        monkeypatch.setattr("server.routes.git.socket.getaddrinfo", fake_getaddrinfo)
        return calls

    return _install


def test_is_safe_mcp_url_gaierror_raises_http_400(resolution_fails):
    """AC#4: a DNS failure (``socket.gaierror``) is refused with HTTP 400."""
    resolution_fails(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(UNRESOLVABLE_URL)

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_gaierror_does_not_escape_as_socket_error(resolution_fails):
    """AC#4: the raw resolver error is converted, not propagated to the caller."""
    resolution_fails(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))

    with pytest.raises(HTTPException):
        _is_safe_mcp_url(UNRESOLVABLE_URL)


def test_is_safe_mcp_url_gaierror_detail_mentions_resolution(resolution_fails):
    """The 400 explains *why* it failed, so the caller can log a useful reason."""
    resolution_fails(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(UNRESOLVABLE_URL)

    assert "resolv" in str(excinfo.value.detail).lower()


def test_is_safe_mcp_url_attempts_to_resolve_the_url_hostname(resolution_fails):
    """The refusal comes from resolving this URL's host, not from the deny list."""
    calls = resolution_fails(socket.gaierror(socket.EAI_NONAME, "unknown host"))

    with pytest.raises(HTTPException):
        _is_safe_mcp_url(UNRESOLVABLE_URL)

    assert calls == ["no-such-host.invalid"]


def test_is_safe_mcp_url_empty_resolution_result_raises_http_400(
    resolution_returns_nothing,
):
    """AC#4: a resolver that answers with zero addresses is also unsafe."""
    resolution_returns_nothing()

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(UNRESOLVABLE_URL)

    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
        socket.timeout("resolver timed out"),
        OSError("resolver unavailable"),
        UnicodeError("label too long"),
    ],
    ids=[
        "gaierror-noname",
        "gaierror-again",
        "socket-timeout",
        "oserror",
        "unicode-error",
    ],
)
def test_is_safe_mcp_url_any_resolver_failure_fails_closed(resolution_fails, error):
    """AC#4: every resolver failure shape ends in a 400 -- never a permissive True."""
    resolution_fails(error)

    with pytest.raises(HTTPException) as excinfo:
        _is_safe_mcp_url(UNRESOLVABLE_URL)

    assert excinfo.value.status_code == 400


def test_is_safe_mcp_url_resolvable_host_is_still_accepted(monkeypatch):
    """Control: the fail-closed path is specific to resolution failure.

    Without this, a guard that refused *every* URL would satisfy the tests
    above for the wrong reason.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 0),
            )
        ]

    monkeypatch.setattr("server.routes.git.socket.getaddrinfo", fake_getaddrinfo)

    assert _is_safe_mcp_url("http://mcp.vendor.example.com:8080/sse") is True
