"""SSRF guard for network-enabled test lanes (#359).

The browser / api / integration lanes take their target URL from the
AIFactory handoff (the deployed app URL) and feed it to a stdlib
``urllib`` health-poll and into the test container as
``TFACTORY_TARGET_URL``. Without a guard, a crafted handoff could point a
lane at a cloud-metadata endpoint (``169.254.169.254``), a link-local
address, loopback, or an internal RFC-1918 host and exfiltrate
credentials or reach internal services.

``assert_safe_target_url`` resolves the URL's host and **blocks** any
address in a link-local / metadata / loopback / unique-local range, plus
RFC-1918 private ranges unless ``allow_private=True`` is set explicitly
(e.g. a same-cluster integration target the operator vouches for).

This module is dependency-free (stdlib only) so it imports cleanly in the
runner containers and the backend test venv alike.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeTargetURLError(ValueError):
    """Raised when a target URL resolves to a blocked address range."""


# Cloud-metadata + link-local + IPv6 unique-local. These are NEVER a
# legitimate test target and are blocked unconditionally — this is the core
# SSRF defence (e.g. AWS/GCP/Azure metadata at 169.254.169.254).
_ALWAYS_BLOCKED: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (metadata)
    ipaddress.ip_network("fe80::/10"),  # link-local v6
    ipaddress.ip_network("fd00::/8"),  # unique-local v6
    ipaddress.ip_network("fc00::/7"),  # unique-local v6 (full ULA range)
)

# Loopback — blocked unless allow_loopback=True. AppRuntime's docker-compose
# health-poll legitimately targets localhost, so it opts in; the untrusted
# AIFactory handoff URL does not.
_LOOPBACK: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),  # loopback v4
    ipaddress.ip_network("::1/128"),  # loopback v6
)

# Private ranges — blocked unless allow_private=True.
_PRIVATE: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _resolve_addresses(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve ``host`` to all IP addresses it maps to.

    A literal IP is returned as-is. A hostname is resolved via DNS; every
    returned address is checked (defends against DNS results that mix a
    public and an internal address).
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


def is_safe_target_url(
    url: str, *, allow_private: bool = False, allow_loopback: bool = False
) -> bool:
    """Return True if ``url`` resolves only to allowed addresses.

    Blocks link-local / cloud-metadata and IPv6 unique-local ranges always;
    blocks loopback unless ``allow_loopback``; blocks RFC-1918 private ranges
    unless ``allow_private``. Returns False on malformed URLs or DNS
    resolution failure (fail-closed).
    """
    try:
        assert_safe_target_url(
            url, allow_private=allow_private, allow_loopback=allow_loopback
        )
    except (UnsafeTargetURLError, OSError):
        return False
    return True


def assert_safe_target_url(
    url: str, *, allow_private: bool = False, allow_loopback: bool = False
) -> None:
    """Validate ``url`` for SSRF safety, raising on any blocked address.

    Args:
        url: The target URL (must include an http/https scheme and host).
        allow_private: When True, RFC-1918 private ranges are permitted.
        allow_loopback: When True, loopback (127.0.0.0/8, ::1) is permitted
            — used by the AppRuntime compose health-poll, which legitimately
            targets localhost. Link-local / cloud-metadata is blocked
            regardless of either flag.

    Raises:
        UnsafeTargetURLError: if the URL is malformed or resolves to a
            blocked address.
        OSError: if DNS resolution of the host fails (callers may treat
            this as unsafe / fail-closed).
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeTargetURLError(
            f"unsafe target URL {url!r}: scheme must be http or https"
        )

    host = parts.hostname
    if not host:
        raise UnsafeTargetURLError(f"unsafe target URL {url!r}: missing host")

    blocked = _ALWAYS_BLOCKED
    if not allow_loopback:
        blocked = blocked + _LOOPBACK
    if not allow_private:
        blocked = blocked + _PRIVATE

    for addr in _resolve_addresses(host):
        # Normalise IPv4-mapped IPv6 (::ffff:127.0.0.1) to its IPv4 form so a
        # mapped loopback/metadata address can't slip past the v4 checks.
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped

        for net in blocked:
            if addr.version == net.version and addr in net:
                raise UnsafeTargetURLError(
                    f"unsafe target URL {url!r}: host {host!r} resolves to "
                    f"blocked address {addr} (in {net})."
                )
