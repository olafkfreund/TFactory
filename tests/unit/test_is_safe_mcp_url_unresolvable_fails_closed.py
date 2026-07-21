"""AC#4: An unresolvable host is treated as unsafe (fail closed).

``_is_safe_mcp_url`` is an SSRF guard: it may only return ``True`` once it has
seen every resolved address and found none of them disallowed. When DNS gives
it nothing to inspect it has proven nothing, so it must refuse rather than pass.

This file pins the *fail-closed* property specifically -- that the two
"nothing resolved" paths through ``socket.getaddrinfo`` both terminate in an
``HTTPException(400)`` and that neither can ever reach the ``return True`` at
the end of the function.

Both no-address modes are covered:
  * ``socket.getaddrinfo`` raises ``socket.gaierror`` (NXDOMAIN / no such host)
  * ``socket.getaddrinfo`` returns an empty result list (no addrinfo tuples)

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so ``git.socket`` can be patched at
the import site of the code under test and the import resolves against a
public attribute path.
"""

import socket

import pytest
from fastapi import HTTPException

from server.routes import git


# A syntactically valid http URL whose hostname is in the reserved-for-testing
# .invalid TLD, so the *real* resolver could never answer for it either. The
# resolver is patched in every test regardless -- no test here touches DNS.
UNRESOLVABLE_URL = "http://mcp-does-not-exist.invalid:9931/sse"

GAIERROR = socket.gaierror(socket.EAI_NONAME, "Name or service not known")


@pytest.fixture
def spy_getaddrinfo(monkeypatch):
    """Install a fake ``getaddrinfo`` and record the calls made to it.

    Patching ``git.socket.getaddrinfo`` mocks the dependency of the function
    under test (name resolution), never the function under test itself. The
    returned recorder lets a test prove resolution was actually attempted, so
    a 400 raised by some earlier pre-resolution check cannot masquerade as a
    fail-closed resolution outcome.
    """
    calls: list[tuple] = []

    def _install(*, result=None, raises=None):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            calls.append((host, port))
            if raises is not None:
                raise raises
            return result

        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
        return calls

    return _install


# Both no-address outcomes are the same acceptance criterion, so they share the
# parametrisation rather than being copy-pasted per test.
NO_ADDRESS_MODES = [
    {"raises": GAIERROR},
    {"result": []},
]
NO_ADDRESS_IDS = ["gaierror", "empty-addrinfo-list"]


@pytest.mark.parametrize("mode", NO_ADDRESS_MODES, ids=NO_ADDRESS_IDS)
def test_is_safe_mcp_url_with_no_resolved_addresses_raises_http_400(spy_getaddrinfo, mode):
    """Neither no-address mode may yield anything but a 400 refusal."""
    spy_getaddrinfo(**mode)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("mode", NO_ADDRESS_MODES, ids=NO_ADDRESS_IDS)
def test_is_safe_mcp_url_with_no_resolved_addresses_never_returns(spy_getaddrinfo, mode):
    """Fail-closed proof: the function must raise, not return a verdict.

    A fail-*open* regression would ``return True`` (letting an unverifiable
    host through the SSRF guard) or ``return False`` (a soft refusal callers
    could ignore). ``pytest.raises`` alone would not catch a bare ``return``
    reached before the raise, so the returned value is captured and reported.
    """
    spy_getaddrinfo(**mode)

    with pytest.raises(HTTPException):
        returned = git._is_safe_mcp_url(UNRESOLVABLE_URL)
        pytest.fail(f"expected HTTPException, but the guard returned {returned!r}")


@pytest.mark.parametrize("mode", NO_ADDRESS_MODES, ids=NO_ADDRESS_IDS)
def test_is_safe_mcp_url_refusal_follows_an_actual_resolution_attempt(spy_getaddrinfo, mode):
    """The 400 comes from resolution, not from an earlier scheme/host reject.

    Without this, a guard that rejected the URL before ever calling the
    resolver would still satisfy the status-code assertions above while
    testing nothing about AC#4.
    """
    calls = spy_getaddrinfo(**mode)

    with pytest.raises(HTTPException):
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert [host for host, _port in calls] == ["mcp-does-not-exist.invalid"]


@pytest.mark.parametrize("mode", NO_ADDRESS_MODES, ids=NO_ADDRESS_IDS)
def test_is_safe_mcp_url_refusal_detail_attributes_the_failure_to_resolution(
    spy_getaddrinfo, mode
):
    """The refusal reason names resolution, so operators can debug the log."""
    spy_getaddrinfo(**mode)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert "resolv" in str(exc_info.value.detail).lower()


def test_is_safe_mcp_url_non_gaierror_resolver_failure_also_fails_closed(spy_getaddrinfo):
    """A resolver failure outside ``gaierror`` is still a refusal, not a leak.

    Boundary case: ``socket.gaierror`` is not the only way resolution can blow
    up (``OSError``/``UnicodeError`` escape for malformed or oversized names).
    A bare ``except socket.gaierror`` would let those propagate as a 500 -- or
    worse, be swallowed by a caller -- instead of failing closed with a 400.
    """
    spy_getaddrinfo(raises=OSError("resolver unavailable"))

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(UNRESOLVABLE_URL)

    assert exc_info.value.status_code == 400
