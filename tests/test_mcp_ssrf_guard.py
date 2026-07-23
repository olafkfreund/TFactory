"""SSRF guard for check_mcp_health (#728/#731): resolve the host and block
cloud-metadata / link-local / reserved addresses while allowing loopback +
private (the intended local/LAN MCP use case). DNS is mocked so no real lookups.
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Make the web-server module importable (same shim as test_mcp_status_route.py).
_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.routes.git import _is_safe_mcp_url_host  # noqa: E402


def _gai(*ips):
    """Fake socket.getaddrinfo result: [(family, type, proto, canon, sockaddr)]."""
    return [(0, 0, 0, "", (ip, 0)) for ip in ips]


def test_blocks_cloud_metadata_address():
    with patch("socket.getaddrinfo", return_value=_gai("169.254.169.254")):
        assert _is_safe_mcp_url_host("http://metadata.example/") is False


def test_blocks_ipv6_link_local():
    with patch("socket.getaddrinfo", return_value=_gai("fe80::1")):
        assert _is_safe_mcp_url_host("http://host/") is False


def test_allows_loopback_v4_and_v6():
    # ::1 is also is_reserved — the loopback-before-reserved ordering must allow it.
    with patch("socket.getaddrinfo", return_value=_gai("127.0.0.1")):
        assert _is_safe_mcp_url_host("http://localhost/") is True
    with patch("socket.getaddrinfo", return_value=_gai("::1")):
        assert _is_safe_mcp_url_host("http://ip6-localhost/") is True


def test_allows_private_and_public():
    with patch("socket.getaddrinfo", return_value=_gai("10.0.0.5")):
        assert _is_safe_mcp_url_host("http://lan/") is True
    with patch("socket.getaddrinfo", return_value=_gai("8.8.8.8")):
        assert _is_safe_mcp_url_host("http://public/") is True


def test_blocks_when_any_resolved_address_is_unsafe():
    # A host resolving to a safe AND a metadata address is blocked (DNS-multi).
    with patch("socket.getaddrinfo", return_value=_gai("8.8.8.8", "169.254.169.254")):
        assert _is_safe_mcp_url_host("http://mixed/") is False


def test_unresolvable_host_fails_closed():
    import socket

    with patch("socket.getaddrinfo", side_effect=socket.gaierror()):
        assert _is_safe_mcp_url_host("http://nope/") is False


def test_empty_resolution_fails_closed():
    with patch("socket.getaddrinfo", return_value=[]):
        assert _is_safe_mcp_url_host("http://empty/") is False


def test_no_hostname_fails_closed():
    assert _is_safe_mcp_url_host("not-a-url") is False
