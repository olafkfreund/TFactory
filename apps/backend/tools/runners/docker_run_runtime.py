"""DockerRunRuntime — run a single image as the system-under-test (#233).

The complement to ``AppRuntime`` (docker-compose) for the common "one image"
case: ``docker run -d`` the image, health-poll the ``wait_for`` URLs, expose
``target_url`` for the lane, then ``docker rm -f`` on exit. The image is usually
produced by a ``build:`` step (see ``build_runner``).

Concurrency-safe (RFC-0016 #465): the container name carries a per-run uuid
suffix and each declared container port is published to an OS-assigned free host
port, so two lanes for the same target never collide on name or host port.
``target_url`` / health polls are rewritten to the dynamic host port.

Mirrors AppRuntime's design: ``docker_cmd`` / ``runner_fn`` / ``clock`` are
injectable so tests never spawn a real container.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit

from .free_port import find_free_port


class DockerRunRuntimeError(Exception):
    """Raised when the container fails to start or become healthy."""


@dataclass
class DockerRunResult:
    started: bool
    container_id: str = ""
    stdout: str = ""
    stderr: str = ""


class DockerRunRuntime:
    """Lifecycle wrapper around ``docker run`` for one ``DockerRunTarget``.

    Usage::

        with DockerRunRuntime(target) as rt:
            rt.wait_for_healthy()
            url = rt.target_url
            # run lane against url …
    """

    def __init__(
        self,
        target,  # DockerRunTarget — untyped to avoid a schema import cycle
        *,
        name: str | None = None,
        poll_interval_seconds: float = 2.0,
        docker_cmd: tuple[str, ...] | None = None,
        runner_fn: Callable | None = None,
        clock: Callable[[], float] | None = None,
        port_picker: Callable[[], int] | None = None,
    ) -> None:
        self.target = target
        # Container name is UNIQUE per run (RFC-0016 #465): a fixed
        # ``tfactory-run-{name}`` collides when the same target runs twice
        # concurrently. The short uuid suffix keeps the name readable while
        # guaranteeing two concurrent lanes for the same target never clash; the
        # prefix still avoids colliding with the user's own stack.
        self.container_name = (
            name or f"tfactory-run-{target.name}-{uuid.uuid4().hex[:8]}"
        )
        self.poll_interval = poll_interval_seconds
        self.docker_cmd = docker_cmd or ("docker",)
        self._runner_fn = runner_fn or subprocess.run
        self._clock = clock or time.monotonic
        self._port_picker = port_picker or find_free_port
        self._started = False
        # Map of declared container port -> dynamically allocated host port,
        # populated by start(). Lets target_url / health polls reach the actual
        # host port instead of the (collision-prone) declared one.
        self.host_ports: dict[str, str] = {}

    # ── target URL ────────────────────────────────────────────────────────

    @property
    def target_url(self) -> str | None:
        """The first ``wait_for`` URL — injected as TFACTORY_TARGET_URL.

        Rewritten to the dynamically-allocated host port (RFC-0016 #465) once
        ``start()`` has bound one, so the lane reaches the actual published port
        rather than the (collision-prone) port declared in ``.tfactory.yml``.
        """
        wf = list(getattr(self.target, "wait_for", []) or [])
        if not wf:
            return None
        return self._rewrite_url(wf[0].url)

    def _rewrite_url(self, url: str) -> str:
        """Point *url* at the dynamic host port for its declared container port.

        Declared health URLs reference the *container* port (the left side of a
        ``host:container`` mapping equals the right side in practice). We map the
        URL's port to the host port the OS actually assigned. If the port isn't
        one we remapped (or no mapping was declared), the URL is returned as-is.
        """
        if not self.host_ports:
            return url
        parts = urlsplit(url)
        declared = str(parts.port) if parts.port is not None else None
        host_port = self.host_ports.get(declared) if declared else None
        if host_port is None:
            return url
        hostname = parts.hostname or "localhost"
        netloc = f"{hostname}:{host_port}"
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )

    # ── start / stop ──────────────────────────────────────────────────────

    def start(self) -> DockerRunResult:
        # Remove any stale container with our (unique) name — best-effort guard
        # against a crashed prior run that reused this exact name.
        self._runner_fn(
            [*self.docker_cmd, "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
        )
        argv = [*self.docker_cmd, "run", "-d", "--name", self.container_name]
        # Bind each declared container port to a DYNAMIC free host port instead
        # of the fixed ``host:container`` mapping (RFC-0016 #465). Fixed host
        # ports collide when the same target runs twice concurrently.
        self.host_ports = {}
        for mapping in self.target.ports:
            container_port = mapping.rsplit(":", 1)[-1]
            host_port = str(self._port_picker())
            self.host_ports[container_port] = host_port
            argv += ["-p", f"{host_port}:{container_port}"]
        for key, val in (self.target.env or {}).items():
            argv += ["-e", f"{key}={val}"]
        argv.append(self.target.image)
        if self.target.command:
            argv += list(self.target.command)

        cp = self._runner_fn(argv, capture_output=True, text=True)
        if cp.returncode != 0:
            raise DockerRunRuntimeError(
                f"docker run failed (exit {cp.returncode}): {cp.stderr}"
            )
        self._started = True
        return DockerRunResult(
            started=True,
            container_id=(cp.stdout or "").strip(),
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
        )

    def stop(self) -> None:
        if not self._started:
            return
        self._runner_fn(
            [*self.docker_cmd, "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
        )
        self._started = False

    # ── context manager ───────────────────────────────────────────────────

    def __enter__(self) -> DockerRunRuntime:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # ── health polling ────────────────────────────────────────────────────

    def wait_for_healthy(self) -> None:
        """Poll every ``wait_for`` URL until it returns its expected status.

        Raises DockerRunRuntimeError if any URL doesn't pass before its timeout.
        """
        for wf in getattr(self.target, "wait_for", []) or []:
            if not self._poll_one(wf):
                raise DockerRunRuntimeError(
                    f"container not healthy: {wf.url} did not return "
                    f"{wf.expect_status} within {wf.timeout_seconds}s"
                )

    def _poll_one(self, wait_for) -> bool:
        url = self._rewrite_url(wait_for.url)
        deadline = self._clock() + wait_for.timeout_seconds
        while self._clock() < deadline:
            try:
                req = urlrequest.Request(url, method="HEAD")
                with urlrequest.urlopen(req, timeout=5) as resp:  # noqa: S310
                    if resp.status == wait_for.expect_status:
                        return True
            except HTTPError as exc:
                if exc.code == wait_for.expect_status:
                    return True
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(self.poll_interval)
        return False
