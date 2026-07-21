"""AC#6: ``_is_safe_mcp_url`` must reject the cloud metadata endpoints.

This file pins the *hostname blocklist* branch specifically: both
``169.254.169.254`` (AWS/Azure IMDS) and ``metadata.google.internal`` (GCP)
are refused by hostname, **before** ``socket.getaddrinfo`` is ever consulted.

Why pre-resolution matters, and why it is asserted rather than assumed:

* A DNS-based defence is defeatable. ``metadata.google.internal`` resolves to
  ``169.254.169.254`` today, but an attacker-controlled or poisoned resolver
  can answer with a benign-looking public address on the first lookup and the
  link-local address on the connect that follows (DNS rebinding). A hostname
  blocklist that runs first is immune to that race.
* Resolution is also the slow, failure-prone step; refusing known-bad hosts
  before it keeps the guard cheap and deterministic.

So every test here installs a resolver that would return a *safe* public
address, and asserts both that the call is refused and that the resolver saw
zero calls. If the blocklist were moved after resolution, the refusal would
still happen for the real hosts but ``calls == 0`` would fail — which is the
regression this file exists to catch.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path. This matches the sibling tests for
this spec.
"""

import socket

import pytest
from fastapi import HTTPException

from server.routes import git


# Deliberately safe: TEST-NET-3 (RFC 5737), a documentation range that is
# public as far as the ipaddress module is concerned. If the guard ever
# resolved the metadata hosts, this is what it would see -- and it would
# wrongly accept.
BENIGN_PUBLIC_ADDRESS = "203.0.113.42"

METADATA_HOSTS = ("169.254.169.254", "metadata.google.internal")


@pytest.fixture
def resolver(monkeypatch):
    """Install a counting ``getaddrinfo`` that always answers with a safe IP.

    Patched at the import site (``git.socket``), per the project convention.
    The returned object exposes ``.calls`` so a test can prove a URL was
    refused without the resolver ever being consulted.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        fake_getaddrinfo.calls += 1
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (BENIGN_PUBLIC_ADDRESS, 0),
            )
        ]

    fake_getaddrinfo.calls = 0
    monkeypatch.setattr(git.socket, "getaddrinfo", fake_getaddrinfo)
    return fake_getaddrinfo


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://169.254.169.254:80/mcp",
        "https://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal",
        "http://metadata.google.internal/computeMetadata/v1/instance/",
        "http://metadata.google.internal:3103/mcp",
        "https://metadata.google.internal/computeMetadata/v1/",
    ],
    ids=[
        "imds-bare",
        "imds-credentials-path",
        "imds-explicit-port",
        "imds-https",
        "gcp-bare",
        "gcp-metadata-path",
        "gcp-explicit-port",
        "gcp-https",
    ],
)
def test_is_safe_mcp_url_metadata_host_raises_http_400(resolver, url):
    """Every metadata-endpoint URL shape is refused with HTTP 400.

    Path, port and scheme are all irrelevant to the decision: the hostname
    alone condemns the URL, so none of these variants may sneak through.
    """
    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(url)

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("host", METADATA_HOSTS, ids=list(METADATA_HOSTS))
def test_is_safe_mcp_url_metadata_host_refused_before_dns_resolution(resolver, host):
    """AC#6: the refusal happens with zero resolver calls (pre-resolution).

    The installed resolver would hand back a benign public address, so if the
    blocklist ran *after* resolution this URL would be accepted outright.
    """
    with pytest.raises(HTTPException):
        git._is_safe_mcp_url(f"http://{host}/mcp")

    assert resolver.calls == 0


@pytest.mark.parametrize("host", METADATA_HOSTS, ids=list(METADATA_HOSTS))
def test_is_safe_mcp_url_metadata_host_detail_reports_disallowed(resolver, host):
    """The 400 is attributed to the blocklist, not to a malformed URL.

    Distinguishing "disallowed" from "invalid" keeps the operator-facing log
    honest and stops a scheme-parsing bug from masquerading as SSRF defence.
    """
    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(f"http://{host}/mcp")

    assert "disallowed" in str(exc_info.value.detail).lower()


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/",
        "HTTP://169.254.169.254/",
        "http://METADATA.GOOGLE.INTERNAL/",
        "http://Metadata.Google.Internal/",
        "  http://metadata.google.internal/  ",
    ],
    ids=[
        "imds-lowercase-scheme",
        "imds-uppercase-scheme",
        "gcp-uppercase-host",
        "gcp-mixed-case-host",
        "gcp-surrounding-whitespace",
    ],
)
def test_is_safe_mcp_url_metadata_host_refused_despite_case_or_whitespace(
    resolver, url
):
    """Case folding and stray whitespace must not defeat the blocklist.

    ``urlparse`` lowercases the hostname and the guard strips the input, so an
    attacker cannot bypass a case-sensitive membership test by shouting the
    host name or padding the value. Pinning it here means a future refactor
    that compares the raw netloc instead of ``parsed.hostname`` fails loudly.
    """
    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(url)

    assert exc_info.value.status_code == 400
    assert resolver.calls == 0


def test_is_safe_mcp_url_metadata_host_never_returns_a_value(resolver):
    """Fail-closed proof: the helper raises, it does not return ``True``.

    ``_is_safe_mcp_url`` is annotated ``-> bool``, so a careless "return False"
    refactor would still typecheck while callers that only branch on falsiness
    kept working -- but callers that ignore the return value (as
    ``check_mcp_health`` does) would silently start allowing IMDS.
    """
    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url("http://169.254.169.254/mcp")
        pytest.fail(f"expected HTTPException, got return value {result!r}")


def test_is_safe_mcp_url_non_metadata_host_still_reaches_the_resolver(resolver):
    """Control: the blocklist is narrow, not a blanket refusal.

    Without this, every assertion above would still pass if the helper simply
    rejected all URLs. A public host must get past the hostname check, consult
    the resolver, and be accepted.
    """
    assert git._is_safe_mcp_url("http://mcp.example.com:3103/mcp") is True
    assert resolver.calls == 1
