"""SSRF guard on the MCP health check (#728/#731, folded into the shared guard
by #1047), plus the route-binding regression that made it moot.

Two things are pinned here.

1. ``_safe_mcp_url`` is now a thin wrapper over the shared
   ``assert_safe_outbound_url`` in the permissive posture, so it must refuse the
   cloud-metadata range and non-http(s) schemes while still allowing the
   loopback/LAN addresses real MCP servers live on. It RETURNS the value to
   fetch, which is what makes the CodeQL barrier meaningful -- a call site
   cannot check one string and request another.

2. ``POST /api/mcp/health`` must be bound to ``check_mcp_health``. Between #794
   and #1047 it was bound to the private guard helper instead, because that PR
   inserted a new function directly beneath the decorator. The endpoint took a
   ``url`` query string and returned a bare bool, so the frontend's
   ``post('/mcp/health', server)`` got a 422 and the health fetch below was
   unreachable. A test that only exercises the helper would never have seen it.

DNS is mocked so no real lookups happen.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the web-server module importable (same shim as test_mcp_status_route.py).
_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.routes.git import (  # noqa: E402
    _safe_mcp_url,
    check_mcp_health,
    mcp_router,
)


def _gai(*ips):
    """Fake socket.getaddrinfo result: [(family, type, proto, canon, sockaddr)]."""
    return [(0, 0, 0, "", (ip, 0)) for ip in ips]


# --------------------------------------------------------------------------
# The guard rejects
# --------------------------------------------------------------------------


def test_blocks_cloud_metadata_address():
    with (
        patch("socket.getaddrinfo", return_value=_gai("169.254.169.254")),
        pytest.raises(ValueError, match="metadata"),
    ):
        _safe_mcp_url("http://metadata.example/")


def test_blocks_ipv6_link_local():
    with (
        patch("socket.getaddrinfo", return_value=_gai("fe80::1")),
        pytest.raises(ValueError, match="metadata"),
    ):
        _safe_mcp_url("http://host/")


def test_blocks_when_any_resolved_address_is_metadata():
    """A name resolving to both a public and a metadata address is refused."""
    with (
        patch("socket.getaddrinfo", return_value=_gai("93.184.216.34", "169.254.169.254")),
        pytest.raises(ValueError, match="metadata"),
    ):
        _safe_mcp_url("http://mixed/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "not-a-url"])
def test_blocks_non_http_schemes(url):
    with pytest.raises(ValueError, match="Unsupported or invalid"):
        _safe_mcp_url(url)


def test_unresolvable_host_fails_closed():
    import socket as _socket

    with (
        patch("socket.getaddrinfo", side_effect=_socket.gaierror("nope")),
        pytest.raises(ValueError, match="cannot resolve"),
    ):
        _safe_mcp_url("http://nope/")


# --------------------------------------------------------------------------
# The guard accepts -- the permissive posture is the point
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "10.1.2.3", "93.184.216.34"])
def test_allows_loopback_lan_and_public(ip):
    with patch("socket.getaddrinfo", return_value=_gai(ip)):
        assert _safe_mcp_url("http://server/mcp") == "http://server/mcp"


def test_preserves_path_and_query_drops_fragment():
    """MCP servers legitimately live under a path; the fragment is never sent."""
    with patch("socket.getaddrinfo", return_value=_gai("10.0.0.9")):
        assert (
            _safe_mcp_url("http://srv:3000/mcp?tenant=a#frag")
            == "http://srv:3000/mcp?tenant=a"
        )


# --------------------------------------------------------------------------
# The route binding -- the part #794 broke
# --------------------------------------------------------------------------


def test_health_route_is_bound_to_check_mcp_health():
    matches = [
        r
        for r in mcp_router.routes
        if getattr(r, "path", None) == "/health" and "POST" in getattr(r, "methods", ())
    ]
    assert len(matches) == 1, "POST /health should be registered exactly once"
    assert matches[0].endpoint is check_mcp_health


# --------------------------------------------------------------------------
# Call-site coverage. The tests above all exercise the HELPER, and a mutation
# run proved they stay green when the call site stops using it -- which is the
# failure this whole issue is about. These two assert the guard is WIRED: they
# fail if the fetch goes back to using the raw, unvalidated URL.
# --------------------------------------------------------------------------


def test_check_mcp_health_refuses_metadata_without_fetching():
    import server.routes.git as git_mod

    server = git_mod.McpServerConfig(
        id="s1", name="n", type="http", url="http://169.254.169.254/latest/meta-data/"
    )
    with patch.object(git_mod, "build_no_redirect_opener") as opener:
        result = asyncio.run(check_mcp_health(server))
    opener.assert_not_called()
    assert result["data"]["status"] == "unhealthy"
    assert "metadata" in result["data"]["message"]


def test_probe_models_refuses_metadata_without_fetching():
    """The BYO-endpoint probe attaches the endpoint's API key as a Bearer
    header, so an unguarded URL hands a credential to whatever it reaches."""
    import server.routes.llm_endpoints as llm_mod

    with patch.object(llm_mod, "build_no_redirect_opener") as opener:
        result = llm_mod._probe_models(
            "http://169.254.169.254/latest/meta-data", "sk-secret", None, timeout=1
        )
    opener.assert_not_called()
    assert result.ok is False
    assert "metadata" in (result.error or "")


def test_check_mcp_health_fetches_the_guard_s_return_value():
    """Checking one string and fetching another is the exact bug the guard's
    return value exists to prevent, and refusal tests cannot see it -- on a URL
    the guard ACCEPTS both variants behave the same. So assert on the URL that
    actually reached the transport: the fragment must already be gone."""
    import server.routes.git as git_mod

    server = git_mod.McpServerConfig(
        id="s1", name="n", type="http", url="http://127.0.0.1:3000/mcp#dropped"
    )
    with patch.object(git_mod, "build_no_redirect_opener") as opener:
        asyncio.run(check_mcp_health(server))
    (req,) = opener.return_value.open.call_args.args
    assert req.full_url == "http://127.0.0.1:3000/mcp"
