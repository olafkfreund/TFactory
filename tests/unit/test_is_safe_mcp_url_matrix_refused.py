"""AC#6: The refused half of the URL matrix — ``_is_safe_mcp_url`` must refuse
``http://169.254.169.254`` (the cloud metadata endpoint), an IPv6 link-local
literal host, and a ``file://`` URL.

Each of the three is refused for a *different* reason, and this file pins the
reason as well as the refusal, so that a regression which collapses all three
into one blanket rejection (or which accepts one of them) is visible:

* ``169.254.169.254``  — refused by hostname, BEFORE any DNS resolution
* ``[fe80::1]``        — refused after resolution, as a link-local address
* ``file:///etc/passwd`` — refused as an unsupported scheme, no hostname

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import ipaddress
import socket

import pytest
from fastapi import HTTPException

from server.routes import git


METADATA_URL = "http://169.254.169.254/latest/meta-data/"
IPV6_LINK_LOCAL_URL = "http://[fe80::1]:3103/mcp"
FILE_URL = "file:///etc/passwd"


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
    """Patch getaddrinfo at the import site and record how often it was called.

    Returns an installer; the installed fake exposes ``.calls`` so a test can
    prove a URL was refused *without* the resolver ever being consulted.
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
    "url,resolved",
    [
        (METADATA_URL, ("169.254.169.254",)),
        (IPV6_LINK_LOCAL_URL, ("fe80::1",)),
        (FILE_URL, ()),
    ],
    ids=["ipv4-metadata-169.254.169.254", "ipv6-link-local-literal", "file-scheme"],
)
def test_is_safe_mcp_url_refused_url_raises_http_400(resolve_to, url, resolved):
    """Every URL in the refused matrix raises HTTPException 400, never returns."""
    resolve_to(*resolved)

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(url)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "url,resolved",
    [
        (METADATA_URL, ("169.254.169.254",)),
        (IPV6_LINK_LOCAL_URL, ("fe80::1",)),
        (FILE_URL, ()),
    ],
    ids=["ipv4-metadata-169.254.169.254", "ipv6-link-local-literal", "file-scheme"],
)
def test_is_safe_mcp_url_refused_url_never_returns_true(resolve_to, url, resolved):
    """Fail-closed proof: no refused URL may yield a truthy accept instead of raising."""
    resolve_to(*resolved)

    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url(url)
        pytest.fail(f"expected HTTPException, got return value {result!r}")


def test_is_safe_mcp_url_metadata_host_refused_before_dns_resolution(resolve_to):
    """AC#6: 169.254.169.254 is blocked by hostname, so DNS is never consulted.

    Blocking pre-resolution matters: it holds even if the resolver is slow,
    poisoned, or would return a benign-looking address for that host.
    """
    fake = resolve_to("203.0.113.42")  # deliberately "safe" — must be irrelevant

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(METADATA_URL)

    assert fake.calls == 0
    assert "disallowed" in str(exc_info.value.detail).lower()


def test_is_safe_mcp_url_ipv6_link_local_literal_refused_as_disallowed_address(
    resolve_to,
):
    """AC#6: an fe80::/10 literal host is refused as a disallowed *address*.

    This is the post-resolution branch, distinct from the hostname blocklist —
    the detail must attribute the refusal to the address, not to a bad scheme.
    """
    resolve_to("fe80::1")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(IPV6_LINK_LOCAL_URL)

    assert "disallowed" in str(exc_info.value.detail).lower()


def test_ipv6_link_local_literal_is_link_local_precondition():
    """Precondition for the test above: ``fe80::1`` really is link-local.

    If this stops holding, the link-local assertion becomes vacuous.
    """
    assert ipaddress.ip_address("fe80::1").is_link_local is True


def test_is_safe_mcp_url_file_scheme_refused_as_invalid_url(resolve_to):
    """AC#6: file:// is refused as an invalid URL, without touching the resolver.

    A ``file://`` URL has no hostname at all, so accepting it would hand the
    downstream health check a local-filesystem read.
    """
    fake = resolve_to("127.0.0.1")

    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(FILE_URL)

    assert fake.calls == 0
    assert "invalid" in str(exc_info.value.detail).lower()
