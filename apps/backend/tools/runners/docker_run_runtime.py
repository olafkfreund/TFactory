"""DockerRunRuntime — run a single image as the system-under-test (#233).

The complement to ``AppRuntime`` (docker-compose) for the common "one image"
case: ``docker run -d`` the image, health-poll the ``wait_for`` URLs, expose
``target_url`` for the lane, then ``docker rm -f`` on exit. The image is usually
produced by a ``build:`` step (see ``build_runner``).

Mirrors AppRuntime's design: ``docker_cmd`` / ``runner_fn`` / ``clock`` are
injectable so tests never spawn a real container.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


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
    ) -> None:
        self.target = target
        # Container name is deterministic per target so a crashed prior run can
        # be force-removed; prefixed to avoid colliding with the user's stack.
        self.container_name = name or f"tfactory-run-{target.name}"
        self.poll_interval = poll_interval_seconds
        self.docker_cmd = docker_cmd or ("docker",)
        self._runner_fn = runner_fn or subprocess.run
        self._clock = clock or time.monotonic
        self._started = False

    # ── target URL ────────────────────────────────────────────────────────

    @property
    def target_url(self) -> str | None:
        """The first ``wait_for`` URL — injected as TFACTORY_TARGET_URL."""
        wf = list(getattr(self.target, "wait_for", []) or [])
        return wf[0].url if wf else None

    # ── start / stop ──────────────────────────────────────────────────────

    def start(self) -> DockerRunResult:
        # Remove any stale container from a crashed prior run (best-effort).
        self._runner_fn(
            [*self.docker_cmd, "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
        )
        argv = [*self.docker_cmd, "run", "-d", "--name", self.container_name]
        for mapping in self.target.ports:
            argv += ["-p", mapping]
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
        deadline = self._clock() + wait_for.timeout_seconds
        while self._clock() < deadline:
            try:
                req = urlrequest.Request(wait_for.url, method="HEAD")
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
