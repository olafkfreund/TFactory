"""AC#4: An unresolvable host is treated as unsafe (fail closed).

``_is_safe_mcp_url`` must never quietly hand back a value when DNS resolution
fails. When ``socket.getaddrinfo`` raises ``socket.gaierror`` (name-resolution
failure) — or when it returns an empty address list (nothing to prove safe) —
the helper must raise ``HTTPException`` with status 400, so an unverifiable
host can never slip through the SSRF guard.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported by name (``from server.routes import git``) and
``getaddrinfo`` is patched at the import site of the module under test, so no
network is touched and only the dependency is mocked — not the function under
test itself.
"""

import socket

import pytest
from fastapi import HTTPException

from server.routes import git


UNRESOLVABLE_URL = "http://no-such-host.invalid:3103/mcp"


@pytest.fixture
def getaddrinfo_stub(monkeypatch):
    """Install a fake ``getaddrinfo`` on the module under test.

    The installed callable either raises the supplied exception or returns the
    supplied result, so one fixture drives both AC#4 failure modes.
    """

    def _install(*, result=None, raises=None):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            if raises is not None:
                raise raises
            return result

        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)

    return _install


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raises": socket.gaierror(socket.EAI_NONAME, "Name or service not known")},
        {"result": []},
    ],
    ids=["gaierror", "empty-result-list"],
)
def test_is_safe_mcp_url_unresolvable_host_raises_http_400(getaddrinfo_stub, kwargs):
    """Resolution failure fails closed with a 400 rather than returning a value."""
    getaddrinfo_stub(**kwargs)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert exc_info.value.status_code == 400


def test_is_safe_mcp_url_gaierror_detail_mentions_resolution(getaddrinfo_stub):
    """The 400 is attributed to resolution, not some unrelated rejection."""
    getaddrinfo_stub(raises=socket.gaierror(socket.EAI_NONAME, "unknown host"))

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert "resolv" in str(exc_info.value.detail).lower()


def test_is_safe_mcp_url_unresolvable_host_never_returns_true(getaddrinfo_stub):
    """Fail-closed proof: a resolution failure can never yield an accepted URL.

    A fail-*open* implementation would return a truthy/falsey value here instead
    of raising, letting an unverifiable host through the SSRF guard.
    """
    getaddrinfo_stub(raises=socket.gaierror(socket.EAI_NONAME, "unknown host"))

    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url(UNRESOLVABLE_URL)
        pytest.fail(f"expected HTTPException, got return value {result!r}")
