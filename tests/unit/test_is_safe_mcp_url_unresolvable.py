"""AC#4: An unresolvable host is treated as unsafe (fail closed).

``_is_safe_mcp_url`` must NOT quietly return a truthy/falsey value when DNS
resolution fails -- it must raise ``HTTPException`` with status 400 both when
``socket.getaddrinfo`` raises ``socket.gaierror`` and when it returns an empty
result list (no addresses to validate ⇒ nothing was proven safe).

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import socket

import pytest
from fastapi import HTTPException

from server.routes import git


UNRESOLVABLE_URL = "http://no-such-host.invalid:3103/mcp"


@pytest.fixture
def getaddrinfo_returns(monkeypatch):
    """Patch getaddrinfo at the import site of the module under test.

    The installed callable either returns the supplied value or raises the
    supplied exception, so a single fixture covers both AC#4 failure modes.
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
def test_is_safe_mcp_url_unresolvable_host_raises_http_400(getaddrinfo_returns, kwargs):
    """Both resolution-failure modes fail closed with a 400, never a pass."""
    getaddrinfo_returns(**kwargs)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raises": socket.gaierror(socket.EAI_NONAME, "Name or service not known")},
        {"result": []},
    ],
    ids=["gaierror", "empty-result-list"],
)
def test_is_safe_mcp_url_unresolvable_host_detail_mentions_resolution(
    getaddrinfo_returns, kwargs
):
    """The 400 is attributed to resolution, not to some unrelated rejection."""
    getaddrinfo_returns(**kwargs)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert "resolv" in str(exc_info.value.detail).lower()


def test_is_safe_mcp_url_unresolvable_host_does_not_return_true(getaddrinfo_returns):
    """Fail-closed proof: a resolution failure can never yield an accepted URL.

    A fail-*open* implementation would return ``True`` (or ``False``) here
    instead of raising, letting an unverifiable host through the SSRF guard.
    """
    getaddrinfo_returns(raises=socket.gaierror(socket.EAI_NONAME, "unknown host"))

    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url(UNRESOLVABLE_URL)
        pytest.fail(f"expected HTTPException, got return value {result!r}")


def test_is_safe_mcp_url_generic_resolution_error_also_fails_closed(
    getaddrinfo_returns,
):
    """A non-gaierror resolver failure (e.g. OSError) is also a 400, not a leak."""
    getaddrinfo_returns(raises=OSError("resolver exploded"))

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert exc_info.value.status_code == 400
