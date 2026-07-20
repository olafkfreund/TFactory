"""SSRF guard for MCP server URLs (#NNN).

MCP servers are typically configured by the operator and often run on local or
LAN endpoints. However, a misconfigured or malicious URL could point to:
- Cloud-metadata endpoints (169.254.169.254)
- Link-local addresses (fe80::/10)
- Multicast ranges
- Unspecified addresses (0.0.0.0, ::)

This module validates MCP server URLs by resolving the hostname and checking
that all returned addresses are in safe ranges. Private and loopback addresses
are explicitly allowed since MCP servers legitimately run on localhost or RFC-1918
ranges; link-local, reserved, multicast, and unspecified ranges are always
blocked.

This module is dependency-free (stdlib only) so it imports cleanly in all
environments.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeMcpUrlError(ValueError):
    """Raised when an MCP server URL resolves to a blocked address range."""


# Link-local and reserved address ranges that are NEVER valid MCP targets.
# These include cloud-metadata endpoints and RFC-reserved ranges.
_ALWAYS_BLOCKED: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (metadata)
    ipaddress.ip_network("fe80::/10"),  # link-local v6
    ipaddress.ip_network("224.0.0.0/4"),  # multicast v4
    ipaddress.ip_network("ff00::/8"),  # multicast v6
    ipaddress.ip_network("0.0.0.0/32"),  # unspecified v4
    ipaddress.ip_network("::/128"),  # unspecified v6
)

# Loopback — always allowed for MCP since servers legitimately run on localhost.
_LOOPBACK: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),  # loopback v4
    ipaddress.ip_network("::1/128"),  # loopback v6
)

# Private ranges — always allowed for MCP since servers often run on the LAN
# (192.168.x.x, 10.x.x.x, etc.).
_PRIVATE: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

# Reserved ranges (RFC 5735, RFC 4291) that are not covered above.
# These include documentation ranges, examples, benchmarks, etc.
_RESERVED: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),  # "this" network
    ipaddress.ip_network("10.0.0.0/8"),  # private (handled separately)
    ipaddress.ip_network("127.0.0.0/8"),  # loopback (handled separately)
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (already blocked)
    ipaddress.ip_network("172.16.0.0/12"),  # private (handled separately)
    ipaddress.ip_network("192.0.0.0/24"),  # test-net-1
    ipaddress.ip_network("192.0.2.0/24"),  # documentation
    ipaddress.ip_network("192.88.99.0/24"),  # nat64
    ipaddress.ip_network("192.168.0.0/16"),  # private (handled separately)
    ipaddress.ip_network("198.18.0.0/15"),  # benchmark
    ipaddress.ip_network("198.51.100.0/24"),  # documentation
    ipaddress.ip_network("203.0.113.0/24"),  # documentation
    ipaddress.ip_network("224.0.0.0/4"),  # multicast (already blocked)
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast
    ipaddress.ip_network("100.64.0.0/10"),  # shared address space
    ipaddress.ip_network("::/128"),  # unspecified (already blocked)
    ipaddress.ip_network("::1/128"),  # loopback (handled separately)
    ipaddress.ip_network("::ffff:0:0/96"),  # ipv4-mapped
    ipaddress.ip_network("64:ff9b::/96"),  # nat64
    ipaddress.ip_network("fc00::/7"),  # unique-local
    ipaddress.ip_network("fe80::/10"),  # link-local (already blocked)
    ipaddress.ip_network("ff00::/8"),  # multicast (already blocked)
)


def _resolve_addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve ``host`` to all IP addresses it maps to.

    A literal IP is returned as-is. A hostname is resolved via DNS; every
    returned address is checked (defends against DNS results that mix a
    public and an internal address).

    Raises OSError if DNS resolution fails (fail-closed interpretation).
    """
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    addrs: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        addrs.append(ipaddress.ip_address(sockaddr[0]))
    return addrs


def is_safe_mcp_url(url: str) -> bool:
    """Return True if ``url`` resolves only to allowed addresses.

    Blocks link-local, reserved, multicast, and unspecified ranges always;
    allows loopback and private ranges. Returns False on malformed URLs or
    DNS resolution failure (fail-closed).
    """
    try:
        assert_safe_mcp_url(url)
    except (UnsafeMcpUrlError, OSError):
        return False
    return True


def assert_safe_mcp_url(url: str) -> None:
    """Validate ``url`` for MCP safety, raising on any blocked address.

    Args:
        url: The MCP server URL (must include an http/https scheme and host).

    Raises:
        UnsafeMcpUrlError: if the URL is malformed or resolves to a
            blocked address.
        OSError: if DNS resolution of the host fails (callers treat
            this as unsafe / fail-closed).
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeMcpUrlError(
            f"unsafe MCP URL {url!r}: scheme must be http or https"
        )

    host = parts.hostname
    if not host:
        raise UnsafeMcpUrlError(f"unsafe MCP URL {url!r}: missing host")

    for addr in _resolve_addresses(host):
        # Normalise IPv4-mapped IPv6 (::ffff:127.0.0.1) to its IPv4 form so a
        # mapped loopback/link-local address can't slip past the v4 checks.
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped

        # Loopback is explicitly allowed (even though ::1 also satisfies is_reserved).
        is_loopback = any(addr in net for net in _LOOPBACK)
        if is_loopback:
            continue

        # Private is explicitly allowed.
        is_private = any(addr in net for net in _PRIVATE)
        if is_private:
            continue

        # Always-blocked ranges (link-local, multicast, unspecified, etc.)
        is_always_blocked = any(addr in net for net in _ALWAYS_BLOCKED)
        if is_always_blocked:
            raise UnsafeMcpUrlError(
                f"unsafe MCP URL {url!r}: host {host!r} resolves to "
                f"blocked address {addr} (in link-local/multicast/unspecified ranges)."
            )

        # Other reserved ranges that are not private or loopback.
        is_reserved = any(addr in net for net in _RESERVED)
        if is_reserved:
            raise UnsafeMcpUrlError(
                f"unsafe MCP URL {url!r}: host {host!r} resolves to "
                f"reserved address {addr}."
            )
