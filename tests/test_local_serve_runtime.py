"""Tests for LocalServeRuntime — the api lane's self-serve host runtime (#612).

A fake ``popen_fn`` stands in for the real process (no uvicorn/subprocess is
spawned), and ``urlrequest.urlopen`` is patched so no real socket is touched
— fast and hermetic. ``os.killpg``/``os.getpgid`` are also patched so the
teardown path is exercised without a real process group. A real end-to-end
smoke (actual subprocess + actual HTTP) was run manually against
``python3 -m http.server`` during development; see the module docstring.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from tools.runners.local_serve_runtime import LocalServeRuntime, LocalServeRuntimeError


class _FakeProc:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.killed_with: list[int] = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _clock_seq(*values):
    it = iter(values)

    def _clock():
        try:
            return next(it)
        except StopIteration:
            return values[-1] + 1000  # push well past any deadline

    return _clock


# ── target_url ────────────────────────────────────────────────────────────


def test_target_url_uses_given_port():
    rt = LocalServeRuntime("python -m uvicorn app:app", Path("/tmp"), 8123)
    assert rt.target_url == "http://127.0.0.1:8123"


# ── start / stop ─────────────────────────────────────────────────────────


def test_start_invokes_popen_with_shell_and_new_session():
    proc = _FakeProc()
    calls = {}

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return proc

    rt = LocalServeRuntime(
        "python -m uvicorn app:app --port 8123",
        Path("/proj"),
        8123,
        popen_fn=fake_popen,
    )
    rt.start()
    assert calls["cmd"] == "python -m uvicorn app:app --port 8123"
    assert calls["kwargs"]["shell"] is True
    assert calls["kwargs"]["cwd"] == "/proj"
    assert calls["kwargs"]["start_new_session"] is True
    assert rt._proc is proc


def test_stop_kills_process_group_when_still_running():
    proc = _FakeProc(pid=555)
    rt = LocalServeRuntime(
        "python -m uvicorn app:app", Path("/tmp"), 8123, popen_fn=lambda *a, **k: proc
    )
    rt.start()
    killed = []
    with (
        patch("os.getpgid", return_value=555),
        patch("os.killpg", side_effect=lambda pgid, sig: killed.append((pgid, sig))),
    ):
        rt.stop()
    assert killed  # a SIGTERM was sent to the process group
    assert rt._proc is None


def test_stop_is_noop_when_process_already_exited():
    proc = _FakeProc()
    proc.returncode = 0  # already exited
    rt = LocalServeRuntime(
        "python -m uvicorn app:app", Path("/tmp"), 8123, popen_fn=lambda *a, **k: proc
    )
    rt.start()
    with patch("os.killpg") as killpg:
        rt.stop()
    killpg.assert_not_called()


def test_stop_is_idempotent_and_never_raises():
    rt = LocalServeRuntime("python -m uvicorn app:app", Path("/tmp"), 8123)
    rt.stop()  # never started — must not raise
    rt.stop()  # calling twice is fine


def test_context_manager_starts_and_tears_down():
    proc = _FakeProc()
    started, stopped = [], []

    def fake_popen(*a, **k):
        started.append(True)
        return proc

    rt = LocalServeRuntime(
        "python -m uvicorn app:app", Path("/tmp"), 8123, popen_fn=fake_popen
    )
    with patch("os.getpgid", return_value=proc.pid), patch("os.killpg"):
        with rt as runtime:
            assert runtime is rt
            assert started
    assert rt._proc is None  # torn down on exit


# ── wait_for_healthy ─────────────────────────────────────────────────────


def test_wait_for_healthy_returns_on_2xx():
    rt = LocalServeRuntime(
        "python -m uvicorn app:app",
        Path("/tmp"),
        8123,
        poll_interval_seconds=0,
        clock=_clock_seq(0.0, 0.1),
    )

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch(
        "tools.runners.local_serve_runtime.urlrequest.urlopen", return_value=_Resp()
    ):
        rt.wait_for_healthy()  # must not raise


def test_wait_for_healthy_treats_any_http_error_as_up():
    """A 404 on '/' still proves the process is listening — an API service
    commonly has no root route, so requiring 2xx would spin to timeout on a
    perfectly healthy app."""
    rt = LocalServeRuntime(
        "python -m uvicorn app:app",
        Path("/tmp"),
        8123,
        poll_interval_seconds=0,
        clock=_clock_seq(0.0, 0.1),
    )

    def raise_404(url, timeout=5):
        raise HTTPError(url, 404, "not found", None, None)

    with patch(
        "tools.runners.local_serve_runtime.urlrequest.urlopen", side_effect=raise_404
    ):
        rt.wait_for_healthy()  # must not raise


def test_wait_for_healthy_raises_when_process_exits_early():
    proc = _FakeProc()
    proc.returncode = 1
    rt = LocalServeRuntime(
        "python -c 'raise SystemExit(1)'",
        Path("/tmp"),
        8123,
        popen_fn=lambda *a, **k: proc,
        clock=_clock_seq(0.0, 0.1),
    )
    rt.start()
    with pytest.raises(LocalServeRuntimeError, match="exited early"):
        rt.wait_for_healthy()


def test_wait_for_healthy_raises_on_timeout():
    rt = LocalServeRuntime(
        "python -m uvicorn app:app",
        Path("/tmp"),
        8123,
        timeout_seconds=5,
        poll_interval_seconds=0,
        clock=_clock_seq(0.0, 1.0, 2.0, 10.0),
    )
    with patch(
        "tools.runners.local_serve_runtime.urlrequest.urlopen",
        side_effect=URLError("connection refused"),
    ):
        with pytest.raises(LocalServeRuntimeError, match="did not become healthy"):
            rt.wait_for_healthy()
