"""Tests for the SSRF guard on MCP server URLs (#728).

Hermetic: literal-IP cases need no DNS; hostname cases monkeypatch
``socket.getaddrinfo`` so no real DNS lookup happens.

Covers:
  - cloud-metadata 169.254.169.254 is blocked (the core case)
  - link-local / IPv6 unique-local ranges are blocked
  - loopback (127.0.0.1, ::1) is allowed (MCP servers often run locally)
  - RFC-1918 private ranges are allowed (MCP servers often run on LAN)
  - IPv4-mapped IPv6 loopback can't bypass the v4 check
  - public addresses pass
  - non-http scheme / missing host rejected
  - DNS failure fails closed (_assert_safe_mcp_url -> OSError)
  - a hostname resolving to metadata is rejected even if it "looks" public
"""

from __future__ import annotations

import socket

import pytest
from server.routes.git import (
    UnsafeMcpURLError,
    _assert_safe_mcp_url,
)


def _patch_resolve(monkeypatch, ip: str) -> None:
    """Force getaddrinfo to resolve any host to ``ip``."""

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ── always-blocked: metadata / link-local / unique-local ───────────────────


def test_cloud_metadata_address_is_blocked():
    """169.254.169.254 (AWS/GCP/Azure metadata) is always blocked."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://169.254.169.254/latest/meta-data/")


def test_link_local_metadata_address_is_blocked():
    """169.254.0.0/16 range (link-local metadata) is always blocked."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://169.254.10.5/")


def test_ipv6_link_local_blocked():
    """fe80::/10 (IPv6 link-local) is always blocked."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://[fe80::1]/")


def test_ipv6_unique_local_fd_blocked():
    """fd00::/8 (IPv6 unique-local) is always blocked."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://[fd00::1]/")


def test_ipv6_unique_local_fc_blocked():
    """fc00::/7 (full IPv6 ULA range) is always blocked."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://[fc00::1]/")


# ── loopback: allowed (MCP servers run locally) ────────────────────────────


def test_loopback_ipv4_allowed():
    """127.0.0.1 (IPv4 loopback) is allowed — MCP servers often run on localhost."""
    _assert_safe_mcp_url("http://127.0.0.1:3000/ready")


def test_loopback_ipv6_allowed():
    """::1 (IPv6 loopback) is allowed — MCP servers often run on localhost."""
    _assert_safe_mcp_url("http://[::1]:3000/ready")


def test_localhost_hostname_allowed(monkeypatch):
    """localhost hostname resolving to 127.0.0.1 is allowed."""
    _patch_resolve(monkeypatch, "127.0.0.1")
    _assert_safe_mcp_url("http://localhost:11434/api/tags")


def test_ipv4_mapped_loopback_cannot_bypass():
    """::ffff:127.0.0.1 must normalise to 127.0.0.1 and stay allowed."""
    _assert_safe_mcp_url("http://[::ffff:127.0.0.1]/")


# ── private: allowed (MCP servers run on LAN) ──────────────────────────────


def test_rfc1918_10_allowed():
    """10.0.0.0/8 (private) is allowed — MCP servers run on LAN."""
    _assert_safe_mcp_url("http://10.1.2.3/")


def test_rfc1918_172_allowed():
    """172.16.0.0/12 (private) is allowed."""
    _assert_safe_mcp_url("http://172.16.1.1/")


def test_rfc1918_192_allowed():
    """192.168.0.0/16 (private) is allowed."""
    _assert_safe_mcp_url("http://192.168.1.1/")


def test_private_hostname_allowed(monkeypatch):
    """Private hostname resolving to 10.x is allowed."""
    _patch_resolve(monkeypatch, "10.0.0.42")
    _assert_safe_mcp_url("https://mcp-server.internal/health")


# ── public passes ──────────────────────────────────────────────────────────


def test_public_address_allowed():
    """Public IPv4 address is allowed."""
    _assert_safe_mcp_url("http://93.184.216.34/")  # example.com IP


def test_public_ipv6_allowed():
    """Public IPv6 address is allowed."""
    _assert_safe_mcp_url("http://[2606:2800:220:1:248:1893:25c8:1946]/")


def test_public_hostname_allowed(monkeypatch):
    """Public hostname resolving to public IP is allowed."""
    _patch_resolve(monkeypatch, "93.184.216.34")
    _assert_safe_mcp_url("https://app.example.com/health")


# ── malformed / DNS failure ────────────────────────────────────────────────


def test_non_http_scheme_rejected():
    """file:// scheme is rejected."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("file:///etc/passwd")


def test_ftp_scheme_rejected():
    """ftp:// scheme is rejected."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("ftp://example.com/")


def test_missing_host_rejected():
    """URL with missing host is rejected."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http:///no-host")


def test_dns_failure_raises_oserror(monkeypatch):
    """DNS failure (socket.gaierror) propagates as OSError."""
    def boom(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(OSError):
        _assert_safe_mcp_url("http://does-not-resolve.invalid/")


def test_hostname_resolving_to_metadata_is_rejected(monkeypatch):
    """DNS-rebinding-style: public-looking name resolving to metadata is rejected."""
    _patch_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://totally-legit.example.com/")


# ── edge cases ─────────────────────────────────────────────────────────────


def test_https_metadata_blocked():
    """https:// scheme doesn't bypass the metadata block."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("https://169.254.169.254/")


def test_url_with_port_and_path():
    """Full URL with port and path validates correctly."""
    _assert_safe_mcp_url("http://127.0.0.1:3000/api/health?query=1")


def test_url_with_credentials_metadata_blocked():
    """URL with credentials (user:pass) still blocks metadata."""
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://user:pass@169.254.169.254/")


def test_ipv4_mapped_metadata_cannot_bypass(monkeypatch):
    """::ffff:169.254.169.254 (IPv4-mapped metadata) is blocked."""
    # This is a constructed case: DNS returning an IPv4-mapped address
    # to a metadata IP. The implementation should normalize it and block.
    with pytest.raises(UnsafeMcpURLError):
        _assert_safe_mcp_url("http://[::ffff:169.254.169.254]/")
