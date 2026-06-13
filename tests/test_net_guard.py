"""Tests for the SSRF guard on network-enabled test lanes (#359).

Hermetic: literal-IP cases need no DNS; hostname cases monkeypatch
``socket.getaddrinfo`` so no real DNS lookup happens.

Covers:
  - cloud-metadata 169.254.169.254 is blocked (the core case)
  - link-local / IPv6 unique-local (fd00::/8) blocked
  - loopback blocked by default, allowed with allow_loopback
  - RFC-1918 private blocked by default, allowed with allow_private
  - IPv4-mapped IPv6 loopback can't bypass the v4 check
  - public addresses pass
  - non-http scheme / missing host rejected
  - DNS failure fails closed (is_safe_target_url -> False)
  - a hostname resolving to metadata is rejected even if it "looks" public
"""

from __future__ import annotations

import socket

import pytest
from tools.runners.net_guard import (
    UnsafeTargetURLError,
    assert_safe_target_url,
    is_safe_target_url,
)


def _patch_resolve(monkeypatch, ip: str) -> None:
    """Force getaddrinfo to resolve any host to ``ip``."""

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ── always-blocked: metadata / link-local / unique-local ───────────────────


def test_cloud_metadata_address_is_blocked():
    with pytest.raises(UnsafeTargetURLError):
        assert_safe_target_url("http://169.254.169.254/latest/meta-data/")
    assert is_safe_target_url("http://169.254.169.254/") is False


def test_link_local_blocked_even_with_all_flags():
    # The metadata/link-local block is unconditional.
    with pytest.raises(UnsafeTargetURLError):
        assert_safe_target_url(
            "http://169.254.10.5/",
            allow_private=True,
            allow_loopback=True,
        )


def test_ipv6_unique_local_blocked():
    with pytest.raises(UnsafeTargetURLError):
        assert_safe_target_url("http://[fd00::1]/")


# ── loopback: gated by allow_loopback ──────────────────────────────────────


def test_loopback_blocked_by_default():
    assert is_safe_target_url("http://127.0.0.1:3000/") is False
    with pytest.raises(UnsafeTargetURLError):
        assert_safe_target_url("http://127.0.0.1:3000/ready")


def test_loopback_allowed_with_flag():
    # AppRuntime's compose health-poll relies on this.
    assert is_safe_target_url("http://127.0.0.1:3000/", allow_loopback=True)


def test_ipv4_mapped_loopback_cannot_bypass():
    # ::ffff:127.0.0.1 must normalise to 127.0.0.1 and stay blocked.
    assert is_safe_target_url("http://[::ffff:127.0.0.1]/") is False


# ── private: gated by allow_private ────────────────────────────────────────


def test_rfc1918_blocked_by_default():
    assert is_safe_target_url("http://10.1.2.3/") is False
    assert is_safe_target_url("http://192.168.1.1/") is False


def test_rfc1918_allowed_with_flag():
    assert is_safe_target_url("http://10.1.2.3/", allow_private=True)


# ── public passes ──────────────────────────────────────────────────────────


def test_public_address_allowed():
    assert is_safe_target_url("http://93.184.216.34/")  # example.com IP


def test_public_hostname_allowed(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    assert is_safe_target_url("https://app.example.com/health")


# ── malformed / DNS failure ────────────────────────────────────────────────


def test_non_http_scheme_rejected():
    with pytest.raises(UnsafeTargetURLError):
        assert_safe_target_url("file:///etc/passwd")
    assert is_safe_target_url("ftp://example.com/") is False


def test_missing_host_rejected():
    with pytest.raises(UnsafeTargetURLError):
        assert_safe_target_url("http:///no-host")


def test_dns_failure_fails_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert is_safe_target_url("http://does-not-resolve.invalid/") is False


def test_hostname_resolving_to_metadata_is_rejected(monkeypatch):
    # DNS-rebinding-style: a public-looking name resolving to metadata.
    _patch_resolve(monkeypatch, "169.254.169.254")
    assert is_safe_target_url("http://totally-legit.example.com/") is False
