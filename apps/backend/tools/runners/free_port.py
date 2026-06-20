"""Free-port allocation for concurrent runtime lanes (RFC-0016 #465).

Concurrent runtime lanes (``DockerRunRuntime`` / ``AppRuntime`` /
``KubernetesRuntime``) must not bind the same fixed host port, or two test runs
for the same target collide. This helper asks the OS for a free TCP port by
binding to port ``0`` on the loopback interface and reading back the port the
kernel assigned, then closing the socket.

There is an unavoidable TOCTOU window between releasing the socket and the
consumer (docker / kubectl) binding the port — another process could grab it in
between. We narrow it by binding ``SO_REUSEADDR`` and handing the port straight
to the consumer; callers that need stronger guarantees should retry on bind
failure. For the concurrency-collision case this fixes (many lanes for the same
target, each asking the OS for a *distinct* free port) this is sufficient: the
kernel will not hand the same port to two simultaneous ``bind(0)`` calls.
"""

from __future__ import annotations

import contextlib
import socket


def find_free_port(host: str = "127.0.0.1") -> int:
    """Return a TCP port the OS reports as free on *host*.

    Binds to port 0 (kernel-assigned), reads the assigned port, then closes the
    socket. See the module docstring for the TOCTOU caveat.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
