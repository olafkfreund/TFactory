"""AC#6: Tests cover an IPv6 link-local literal.

A URL whose host is an IPv6 link-local literal — ``http://[fe80::1]:3103/mcp``
and friends in ``fe80::/10`` — must be refused by ``_is_safe_mcp_url`` with
``HTTPException`` 400, never accepted.

This is the *address*-branch refusal (post-resolution), not the hostname
blocklist that catches ``169.254.169.254``: the literal survives scheme
validation and the metadata blocklist, so the only thing standing between it
and an SSRF is the ``ip.is_link_local`` test inside the per-address loop. The
tests below pin that branch specifically:

* the refusal happens for the whole ``fe80::/10`` range, not just ``fe80::1``
* it is attributed to the *address* ("Disallowed MCP server address"), so a
  regression that starts rejecting the literal as a bad *URL* is visible
* it survives being mixed with allowed (loopback / private) addresses in the
  same ``getaddrinfo`` result — AC#1's "every resolved address is checked"

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, matching the sibling tests for this
spec and keeping the pre-flight import check on a public attribute path.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


IPV6_LINK_LOCAL_URL = "http://[fe80::1]:3103/mcp"

ADDRESS_REFUSAL_DETAIL = "Disallowed MCP server address"


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
    """Patch ``getaddrinfo`` at the import site to return canned addresses.

    Returns an installer; the installed fake records ``.calls`` so a test can
    prove whether the resolver was consulted at all.
    """

    def _install(*addresses):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            fake_getaddrinfo.calls += 1
            return _addrinfo(*addresses)

        fake_getaddrinfo.calls = 0
        monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
        return fake_getaddrinfo

    return _install


@pytest.mark.parametrize(
    "literal",
    [
        "fe80::1",
        "fe80::abcd:1234",
        "febf::1",
    ],
    ids=["fe80-colon-1", "fe80-with-interface-suffix", "febf-top-of-fe80-slash-10"],
)
def test_is_safe_mcp_url_ipv6_link_local_literal_raises_http_400(resolve_to, literal):
    """AC#6: any fe80::/10 literal host is refused with HTTPException 400."""
    resolve_to(literal)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(f"http://[{literal}]:3103/mcp")

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "literal",
    [
        "fe80::1",
        "fe80::abcd:1234",
        "febf::1",
    ],
    ids=["fe80-colon-1", "fe80-with-interface-suffix", "febf-top-of-fe80-slash-10"],
)
def test_ipv6_link_local_literal_is_link_local_precondition(literal):
    """Precondition: each literal under test really is IPv6 link-local.

    Without this, an assertion about the link-local branch could pass while
    actually exercising some other rejection path.
    """
    assert ipaddress.ip_address(literal).is_link_local is True


def test_is_safe_mcp_url_ipv6_link_local_literal_never_returns_true(resolve_to):
    """Fail-closed: the helper must raise, not return a truthy accept."""
    resolve_to("fe80::1")

    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url(IPV6_LINK_LOCAL_URL)
        pytest.fail(f"expected HTTPException, got return value {result!r}")


def test_is_safe_mcp_url_ipv6_link_local_refused_as_address_not_as_bad_url(resolve_to):
    """AC#6: the refusal is attributed to the resolved address, not the URL.

    ``[fe80::1]`` is a syntactically valid http URL, so if the detail ever reads
    "Invalid MCP server URL" the address-range loop has stopped being the thing
    that blocks it — the guard would then be one urlparse change from silently
    letting link-local hosts through.
    """
    resolve_to("fe80::1")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(IPV6_LINK_LOCAL_URL)

    assert exc_info.value.detail == ADDRESS_REFUSAL_DETAIL


def test_is_safe_mcp_url_ipv6_link_local_is_checked_after_resolution(resolve_to):
    """The literal reaches the resolver — it is not caught by the hostname blocklist.

    This distinguishes the fe80:: case from ``169.254.169.254`` (blocked
    pre-resolution) and proves the post-resolution loop is what refuses it.
    """
    fake = resolve_to("fe80::1")

    with pytest.raises(HTTPException):
        git._is_safe_mcp_url(IPV6_LINK_LOCAL_URL)

    assert fake.calls == 1


@pytest.mark.parametrize(
    "resolved",
    [
        ("fe80::1", "127.0.0.1"),
        ("127.0.0.1", "fe80::1"),
        ("10.0.0.5", "fe80::1"),
        ("::1", "fe80::1"),
    ],
    ids=[
        "link-local-first-then-loopback",
        "loopback-first-then-link-local",
        "private-first-then-link-local",
        "ipv6-loopback-first-then-link-local",
    ],
)
def test_is_safe_mcp_url_link_local_among_allowed_addresses_still_refused(
    resolve_to, resolved
):
    """AC#1/AC#6: one link-local address poisons the whole result set.

    A host that resolves to both a benign address and a link-local one must be
    refused regardless of ordering — otherwise a DNS record that lists a
    loopback address first would smuggle an fe80:: target past the guard.
    """
    resolve_to(*resolved)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url("http://mcp.internal.example:3103/mcp")

    assert exc_info.value.detail == ADDRESS_REFUSAL_DETAIL


def test_is_safe_mcp_url_https_ipv6_link_local_literal_also_refused(resolve_to):
    """The scheme is irrelevant: https to a link-local literal is refused too."""
    resolve_to("fe80::1")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url("https://[fe80::1]/mcp")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == ADDRESS_REFUSAL_DETAIL
