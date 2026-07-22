#!/usr/bin/env python3
"""Test _assert_safe_mcp_url function."""

import sys
from pathlib import Path

# Add apps/web-server to path
web_server_path = Path(__file__).parent / 'apps' / 'web-server'
sys.path.insert(0, str(web_server_path))

from server.routes.git import _assert_safe_mcp_url, UnsafeMcpURLError

def test_safe_urls():
    """Test URLs that should be accepted."""
    urls = [
        "http://localhost:8000",
        "http://localhost:3000",
        "http://192.168.1.100:8000",
        "http://10.0.0.1:5000",
        "https://example.com:443",
    ]

    for url in urls:
        try:
            _assert_safe_mcp_url(url)
            print(f"✓ {url} - PASS")
        except Exception as e:
            print(f"✗ {url} - FAIL: {e}")
            return False

    return True


def test_blocked_urls():
    """Test URLs that should be blocked."""
    urls = [
        ("http://169.254.169.254/latest/meta-data", "cloud metadata"),
        ("http://[fe80::1]:8000", "link-local IPv6"),
        ("file:///etc/passwd", "invalid scheme"),
        ("gopher://example.com", "invalid scheme"),
    ]

    for url, reason in urls:
        try:
            _assert_safe_mcp_url(url)
            print(f"✗ {url} ({reason}) - FAIL: should be blocked")
            return False
        except UnsafeMcpURLError:
            print(f"✓ {url} ({reason}) - correctly blocked")
        except Exception as e:
            print(f"✗ {url} ({reason}) - FAIL with unexpected error: {e}")
            return False

    return True


def test_invalid_urls():
    """Test invalid URL formats."""
    urls = [
        ("http://", "missing hostname"),
        ("https://", "missing hostname"),
    ]

    for url, reason in urls:
        try:
            _assert_safe_mcp_url(url)
            print(f"✗ {url} ({reason}) - FAIL: should be invalid")
            return False
        except UnsafeMcpURLError:
            print(f"✓ {url} ({reason}) - correctly rejected")
        except Exception as e:
            print(f"✗ {url} ({reason}) - FAIL with unexpected error: {e}")
            return False

    return True


if __name__ == "__main__":
    print("Testing safe URLs...")
    if not test_safe_urls():
        sys.exit(1)

    print("\nTesting blocked URLs...")
    if not test_blocked_urls():
        sys.exit(1)

    print("\nTesting invalid URLs...")
    if not test_invalid_urls():
        sys.exit(1)

    print("\n✅ All tests passed!")
