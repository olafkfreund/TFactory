#!/usr/bin/env python3
"""Tests for MCP health check: block metadata and link-local addresses (#749).

Covers:
- ``_validate_mcp_url_host`` validates URL host and blocks SSRF attacks
  via link-local (169.254.0.0/16), reserved, multicast, and unspecified ranges
- Loopback (127.0.0.1, ::1) is allowed and checked before reserved ranges
- Private ranges (10/8, 172.16/12, 192.168/16) remain allowed
- Unresolvable hostnames are treated as unsafe (fail closed)
- check_mcp_health endpoint returns status=unknown when URL is refused
- The rejection reason is logged, not exposed to the client
- Test coverage per AC#6: AWS metadata, IPv6 link-local, file://, localhost,
  127.0.0.1, private 10.x, and public hostnames
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import FastAPI, HTTPException  # noqa: E402
from server.routes.git import _validate_mcp_url_host, mcp_router  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: _validate_mcp_url_host
# ---------------------------------------------------------------------------


class TestValidateMcpUrlHost:
    """Tests for the URL host validation helper function."""

    def test_validate_mcp_url_host_rejects_empty_url(self):
        """Empty URL should raise HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_none_url(self):
        """None URL should raise HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host(None)
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_whitespace_url(self):
        """Whitespace-only URL should raise HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("   ")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_file_scheme(self):
        """file:// scheme should be rejected (AC#6)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("file:///etc/passwd")
        assert exc_info.value.status_code == 400
        assert "http" in exc_info.value.detail.lower() or "scheme" in exc_info.value.detail.lower()

    def test_validate_mcp_url_host_rejects_invalid_scheme(self):
        """ftp:// and other schemes should be rejected."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("ftp://example.com")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_no_scheme(self):
        """URL without scheme should be rejected."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("example.com")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_no_hostname(self):
        """URL with scheme but no hostname should be rejected."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_accepts_http_localhost(self):
        """http://localhost should be allowed (loopback, AC#2)."""
        result = _validate_mcp_url_host("http://localhost")
        assert result == "http://localhost"

    def test_validate_mcp_url_host_accepts_https_localhost(self):
        """https://localhost should be allowed (loopback, AC#2)."""
        result = _validate_mcp_url_host("https://localhost")
        assert result == "https://localhost"

    def test_validate_mcp_url_host_accepts_http_127_0_0_1(self):
        """http://127.0.0.1 should be allowed (loopback, AC#2, AC#6)."""
        result = _validate_mcp_url_host("http://127.0.0.1")
        assert result == "http://127.0.0.1"

    def test_validate_mcp_url_host_accepts_http_loopback_with_port(self):
        """http://127.0.0.1:8080 should be allowed."""
        result = _validate_mcp_url_host("http://127.0.0.1:8080")
        assert result == "http://127.0.0.1:8080"

    def test_validate_mcp_url_host_accepts_http_loopback_with_path(self):
        """http://127.0.0.1/path should return scheme://netloc only (drops path)."""
        result = _validate_mcp_url_host("http://127.0.0.1/path/to/resource")
        assert result == "http://127.0.0.1"

    def test_validate_mcp_url_host_accepts_private_10_range(self):
        """Private 10.x addresses should be allowed (AC#3, AC#6)."""
        result = _validate_mcp_url_host("http://10.0.0.1")
        assert result == "http://10.0.0.1"

    def test_validate_mcp_url_host_accepts_private_172_range(self):
        """Private 172.16-172.31 addresses should be allowed (AC#3)."""
        result = _validate_mcp_url_host("http://172.16.0.1")
        assert result == "http://172.16.0.1"

    def test_validate_mcp_url_host_accepts_private_192_range(self):
        """Private 192.168.x.x addresses should be allowed (AC#3)."""
        result = _validate_mcp_url_host("http://192.168.1.1")
        assert result == "http://192.168.1.1"

    def test_validate_mcp_url_host_rejects_aws_metadata_endpoint(self):
        """AWS metadata endpoint (169.254.169.254) should be blocked (AC#1, AC#6)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://169.254.169.254/latest/meta-data/")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_google_metadata_endpoint(self):
        """Google metadata endpoint should be blocked."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://metadata.google.internal")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_link_local_169_254_range(self):
        """Link-local range 169.254.x.x (except .169.254) should be blocked (AC#1)."""
        # 169.254.1.1 is link-local but not the AWS metadata endpoint
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://169.254.1.1")
        assert exc_info.value.status_code == 400
        # Check it's rejected as link-local, not metadata endpoint
        assert "link-local" in exc_info.value.detail.lower() or "invalid" in exc_info.value.detail.lower()

    def test_validate_mcp_url_host_rejects_ipv6_link_local(self):
        """IPv6 link-local literals should be blocked (AC#1, AC#6)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://[fe80::1]")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_ipv6_link_local_with_zone(self):
        """IPv6 link-local with zone ID should be blocked."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://[fe80::1%eth0]")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_ipv6_multicast(self):
        """IPv6 multicast should be blocked (AC#1)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://[ff00::1]")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_rejects_ipv6_unspecified(self):
        """IPv6 unspecified (::) should be blocked (AC#1)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://[::]")
        assert exc_info.value.status_code == 400

    def test_validate_mcp_url_host_accepts_ipv6_loopback(self):
        """IPv6 loopback (::1) should be allowed (AC#2)."""
        result = _validate_mcp_url_host("http://[::1]")
        assert result == "http://[::1]"

    def test_validate_mcp_url_host_accepts_ipv6_private(self):
        """IPv6 private (ULA) should be allowed (AC#3)."""
        result = _validate_mcp_url_host("http://[fd00::1]")
        assert result == "http://[fd00::1]"

    def test_validate_mcp_url_host_drops_path_and_query(self):
        """Path and query should be stripped from result (AC#5 spirit)."""
        result = _validate_mcp_url_host("http://example.com:8080/health?check=true#fragment")
        assert result == "http://example.com:8080"
        assert "/health" not in result
        assert "?check=true" not in result
        assert "#fragment" not in result

    def test_validate_mcp_url_host_preserves_port(self):
        """Port should be preserved in the netloc."""
        result = _validate_mcp_url_host("http://example.com:3100")
        assert ":3100" in result

    @patch("socket.getaddrinfo")
    def test_validate_mcp_url_host_rejects_unresolvable_hostname(self, mock_getaddrinfo):
        """Unresolvable hostnames should be treated as unsafe (AC#4)."""
        mock_getaddrinfo.side_effect = socket.gaierror("Name or service not known")

        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://nonexistent.example.invalid")
        assert exc_info.value.status_code == 400

    @patch("socket.getaddrinfo")
    def test_validate_mcp_url_host_checks_all_resolved_addresses(self, mock_getaddrinfo):
        """All resolved addresses should be checked, not just the first (AC#1)."""
        # Mock getaddrinfo to return multiple addresses, some good and one bad
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80)),  # bad
        ]

        with pytest.raises(HTTPException) as exc_info:
            _validate_mcp_url_host("http://example.com")
        assert exc_info.value.status_code == 400

    @patch("socket.getaddrinfo")
    def test_validate_mcp_url_host_allows_if_all_addresses_safe(self, mock_getaddrinfo):
        """If all resolved addresses are safe, should allow (AC#1)."""
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("5.6.7.8", 80)),
        ]

        result = _validate_mcp_url_host("http://example.com")
        assert result == "http://example.com"

    @patch("socket.getaddrinfo")
    def test_validate_mcp_url_host_loopback_checked_before_reserved(self, mock_getaddrinfo):
        """Loopback should be checked before reserved test (AC#2, IPv6 ::1 is reserved)."""
        # IPv6 ::1 is both loopback AND reserved, so order matters
        mock_getaddrinfo.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 80, 0, 0)),
        ]

        result = _validate_mcp_url_host("http://[::1]")
        assert result == "http://[::1]"  # Should allow because loopback is checked first


# ---------------------------------------------------------------------------
# Endpoint: check_mcp_health
# ---------------------------------------------------------------------------


class TestCheckMcpHealthEndpoint:
    """Tests for the check_mcp_health endpoint."""

    @pytest.fixture
    def app(self):
        """Create a test FastAPI app with the MCP router."""
        app = FastAPI()
        app.include_router(mcp_router, prefix="/api/mcp")
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_check_mcp_health_rejects_aws_metadata_endpoint(self, client):
        """check_mcp_health should reject AWS metadata endpoint (AC#6)."""
        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "http://169.254.169.254/latest/meta-data/",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "unknown"
        # Reason should not be exposed to client (AC#5)
        assert "validation" not in data["data"]["message"].lower() or "url" not in data["data"]["message"].lower()

    def test_check_mcp_health_rejects_ipv6_link_local(self, client):
        """check_mcp_health should reject IPv6 link-local (AC#6)."""
        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "http://[fe80::1]/health",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "unknown"

    def test_check_mcp_health_rejects_file_scheme(self, client):
        """check_mcp_health should reject file:// scheme (AC#6)."""
        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "file:///etc/passwd",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "unknown"

    @patch("urllib.request.urlopen")
    def test_check_mcp_health_allows_http_localhost(self, mock_urlopen, client):
        """check_mcp_health should allow localhost (AC#6)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response

        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "http://localhost:8000",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "healthy"

    @patch("urllib.request.urlopen")
    def test_check_mcp_health_allows_http_127_0_0_1(self, mock_urlopen, client):
        """check_mcp_health should allow 127.0.0.1 (AC#6)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response

        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "http://127.0.0.1:3000",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "healthy"

    @patch("urllib.request.urlopen")
    def test_check_mcp_health_allows_private_10_range(self, mock_urlopen, client):
        """check_mcp_health should allow private 10.x (AC#6)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response

        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "http://10.0.0.100:5000",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "healthy"

    @patch("urllib.request.urlopen")
    @patch("socket.getaddrinfo")
    def test_check_mcp_health_allows_public_hostname(self, mock_getaddrinfo, mock_urlopen, client):
        """check_mcp_health should allow public hostnames (AC#6)."""
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 80)),
        ]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response

        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "http",
                "url": "http://api.example.com:80",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "healthy"

    def test_check_mcp_health_rejected_url_logs_reason(self, client, caplog):
        """Rejection reason should be logged, not returned to client (AC#5)."""
        with caplog.at_level(logging.WARNING):
            response = client.post(
                "/api/mcp/health",
                json={
                    "id": "test-server",
                    "name": "Test",
                    "type": "http",
                    "url": "http://169.254.169.254/",
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "unknown"

        # Check that reason was logged (AC#5)
        assert any("validation" in record.message.lower() for record in caplog.records)

    def test_check_mcp_health_returns_server_id_in_response(self, client):
        """Response should include the server ID (AC#5 response shape)."""
        response = client.post(
            "/api/mcp/health",
            json={
                "id": "my-mcp-server",
                "name": "Test",
                "type": "http",
                "url": "http://169.254.169.254/",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["serverId"] == "my-mcp-server"

    def test_check_mcp_health_unknown_for_command_type(self, client):
        """Command-based servers should return status: unknown."""
        response = client.post(
            "/api/mcp/health",
            json={
                "id": "test-server",
                "name": "Test",
                "type": "command",
                "command": "npx",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "unknown"
