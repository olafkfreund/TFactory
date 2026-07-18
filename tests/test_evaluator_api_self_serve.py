"""Tests for the api lane's self-serve fallback (#612).

Spec-ingest tasks have no ``.tfactory.yml`` target configured, so
``_browser_target_url`` returns None and the api lane previously had nothing
to run against (VAL-2 permanently unreachable). ``_maybe_self_serve_api_bundle``
detects the app's entrypoint and boots it on a free host port instead.

``LocalServeRuntime``/``find_free_port`` are imported *inside* the function
under test, so they're patched at their source module (the local import
resolves there at call time) rather than on ``agents.evaluator``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agents.evaluator import (
    _API_SELF_SERVE_PORT,
    _build_kube_or_static_bundle,
    _host_serve_command,
    _maybe_self_serve_api_bundle,
    _resolve_runner_fn,
)
from tools.runners.docker_runner import DockerRunResult
from tools.runners.local_serve_runtime import LocalServeRuntimeError


class _FakeRuntime:
    """Stands in for LocalServeRuntime — records lifecycle calls, no process."""

    instances: list[_FakeRuntime] = []

    def __init__(self, serve_command, project_dir, port, **kwargs):
        self.serve_command = serve_command
        self.project_dir = project_dir
        self.port = port
        self.target_url = f"http://127.0.0.1:{port}"
        self.started = False
        self.stopped = False
        self.healthy_error: Exception | None = None
        _FakeRuntime.instances.append(self)

    def wait_for_healthy(self):
        if self.healthy_error:
            raise self.healthy_error

    def __enter__(self):
        self.started = True
        return self

    def __exit__(self, *a):
        self.stopped = True
        return False


def setup_function():
    _FakeRuntime.instances.clear()


def _patched(**kw):
    """Patch the seams _maybe_self_serve_api_bundle imports locally."""
    p1 = patch("tools.runners.local_serve_runtime.LocalServeRuntime", _FakeRuntime)
    p2 = patch("tools.runners.free_port.find_free_port", return_value=9999)
    p3 = patch("agents.evaluator.detect_serve_command", **kw) if kw else None
    return p1, p2, p3


# ── _maybe_self_serve_api_bundle ────────────────────────────────────────


def test_skipped_for_non_api_lane(tmp_path):
    st = {"id": "x", "lane": "unit"}
    result = _maybe_self_serve_api_bundle(
        tmp_path, tmp_path, st, make_runner=lambda u: u, make_bundle=lambda r: r
    )
    assert result is None


def test_skipped_when_nixjob_backend_selected(tmp_path, monkeypatch):
    """The Nix Job path executes in a separate pod — a 127.0.0.1 URL bound on
    the evaluator's own host is unreachable there, so self-serve must not
    engage (falls through to the existing honest not_run path)."""
    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda spec_dir: True)
    calls = []
    monkeypatch.setattr(
        "agents.evaluator.detect_serve_command",
        lambda *a, **k: calls.append(1) or "python -m uvicorn app:app",
    )
    st = {"id": "x", "lane": "api"}
    result = _maybe_self_serve_api_bundle(
        tmp_path, tmp_path, st, make_runner=lambda u: u, make_bundle=lambda r: r
    )
    assert result is None
    assert not calls  # never even tried to detect a serve command


def test_returns_none_when_no_serve_command_detected(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda spec_dir: False)
    monkeypatch.setattr("agents.evaluator.detect_serve_command", lambda *a, **k: None)
    st = {"id": "x", "lane": "api"}
    result = _maybe_self_serve_api_bundle(
        tmp_path, tmp_path, st, make_runner=lambda u: u, make_bundle=lambda r: r
    )
    assert result is None


def test_happy_path_boots_serves_and_tears_down(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda spec_dir: False)
    monkeypatch.setattr(
        "agents.evaluator.detect_serve_command",
        lambda project_dir, env, port=8099: f"python -m uvicorn app:app --port {port}",
    )
    monkeypatch.setattr(
        "agents.evaluator._host_serve_command", lambda cmd, project_dir: cmd
    )
    st = {"id": "create-item", "lane": "api"}
    seen_urls = []

    def make_runner(url):
        seen_urls.append(url)
        return f"runner-for-{url}"

    def make_bundle(runner):
        return f"bundle[{runner}]"

    p1, p2, _ = _patched()
    with p1, p2:
        result = _maybe_self_serve_api_bundle(
            tmp_path, tmp_path, st, make_runner=make_runner, make_bundle=make_bundle
        )

    assert result == "bundle[runner-for-http://127.0.0.1:9999]"
    assert seen_urls == ["http://127.0.0.1:9999"]
    assert len(_FakeRuntime.instances) == 1
    rt = _FakeRuntime.instances[0]
    assert rt.started and rt.stopped  # boot + teardown both happened
    assert rt.port == 9999


def test_returns_none_and_tears_down_when_health_check_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda spec_dir: False)
    monkeypatch.setattr(
        "agents.evaluator.detect_serve_command",
        lambda project_dir, env, port=8099: "python -m uvicorn app:app",
    )
    monkeypatch.setattr(
        "agents.evaluator._host_serve_command", lambda cmd, project_dir: cmd
    )

    def _make_unhealthy_runtime(serve_command, project_dir, port, **kwargs):
        rt = _FakeRuntime(serve_command, project_dir, port, **kwargs)
        rt.healthy_error = LocalServeRuntimeError("never came up")
        return rt

    st = {"id": "x", "lane": "api"}
    p1 = patch(
        "tools.runners.local_serve_runtime.LocalServeRuntime", _make_unhealthy_runtime
    )
    p2 = patch("tools.runners.free_port.find_free_port", return_value=9999)
    with p1, p2:
        result = _maybe_self_serve_api_bundle(
            tmp_path, tmp_path, st, make_runner=lambda u: u, make_bundle=lambda r: r
        )
    assert result is None
    assert _FakeRuntime.instances[0].stopped  # teardown still ran despite the failure


# ── _host_serve_command ───────────────────────────────────────────────────


def test_host_serve_command_rewrites_python_to_venv_python(tmp_path, monkeypatch):
    venv_dir = tmp_path / "venv"
    monkeypatch.setattr("agents.evaluator._ensure_host_venv", lambda pd: venv_dir)
    cmd = _host_serve_command("python -m uvicorn app:app --port 8123", tmp_path)
    assert cmd == f"{venv_dir / 'bin' / 'python'} -m uvicorn app:app --port 8123"


def test_host_serve_command_leaves_non_python_command_unchanged():
    assert _host_serve_command("npm start", Path("/proj")) == "npm start"


# ── api lane boots the SUT INSIDE the Nix Job (#612 reopened) ──────────────
#
# In nix mode the host self-serve (_maybe_self_serve_api_bundle) is skipped —
# a 127.0.0.1 URL bound on the control plane can't reach the separate Job pod.
# Instead the runner_fn detects the serve command and threads it into
# _maybe_nix_verify so the Job boots the app in-pod and sets TFACTORY_TARGET_URL.


def _drive_runner(tmp_path, monkeypatch, *, network, target_url):
    """Call the api/unit runner_fn with _maybe_nix_verify captured, return kwargs."""
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "api_test.py"
    test_file.write_text("import os\nBASE=os.environ['TFACTORY_TARGET_URL']\n")

    captured = {}

    def _fake_nix_verify(spec_dir, project_dir, tf, extra_env, **kw):
        captured.update(kw)
        return DockerRunResult(returncode=0, stdout="__PYTEST_EXIT=0", stderr="")

    monkeypatch.setattr("agents.evaluator._maybe_nix_verify", _fake_nix_verify)
    monkeypatch.setattr(
        "agents.evaluator.detect_serve_command",
        lambda pd, env, port=8099: f"python -m uvicorn app.main:app --port {port}",
    )
    monkeypatch.setattr(
        "agents.evaluator.environment_from_contract", lambda spec_dir: {}
    )

    runner = _resolve_runner_fn(
        tmp_path,
        project,
        network=network,
        target_url=target_url,
        subtask={"id": "s", "lane": "api" if network == "host" else "unit"},
    )
    res = runner(test_file, project, 424242)
    assert res.returncode == 0  # short-circuited on the captured nix result
    return captured


def test_api_lane_threads_serve_command_and_target_url_into_nix_job(
    tmp_path, monkeypatch
):
    captured = _drive_runner(tmp_path, monkeypatch, network="host", target_url=None)
    # the app-boot IS invoked (serve command detected for the port the Job serves)
    assert captured["serve_command"] == (
        f"python -m uvicorn app.main:app --port {_API_SELF_SERVE_PORT}"
    )
    assert captured["serve_port"] == _API_SELF_SERVE_PORT


def test_unit_lane_never_boots_an_app_in_the_nix_job(tmp_path, monkeypatch):
    captured = _drive_runner(tmp_path, monkeypatch, network="none", target_url=None)
    assert captured["serve_command"] is None


def test_api_lane_with_external_target_does_not_self_serve(tmp_path, monkeypatch):
    """A configured target URL means the test hits the remote app — no in-Job
    boot (that URL is already injected as TFACTORY_TARGET_URL upstream)."""
    captured = _drive_runner(
        tmp_path, monkeypatch, network="host", target_url="https://staging.example"
    )
    assert captured["serve_command"] is None


# ── _build_kube_or_static_bundle wiring ────────────────────────────────────


def test_build_kube_or_static_bundle_falls_through_to_self_serve(tmp_path, monkeypatch):
    """No .tfactory.yml at all (spec-ingest) + lane=api must reach self-serve,
    not silently run with target_url=None."""
    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda spec_dir: False)
    monkeypatch.setattr(
        "agents.evaluator.detect_serve_command",
        lambda project_dir, env, port=8099: f"python -m uvicorn app:app --port {port}",
    )
    monkeypatch.setattr(
        "agents.evaluator._host_serve_command", lambda cmd, project_dir: cmd
    )
    st = {"id": "x", "lane": "api"}

    p1, p2, _ = _patched()
    with p1, p2:
        result = _build_kube_or_static_bundle(
            tmp_path,
            tmp_path,
            st,
            make_runner=lambda url: url,
            make_bundle=lambda runner: f"bundle[{runner}]",
        )
    assert result == "bundle[http://127.0.0.1:9999]"


def test_build_kube_or_static_bundle_stays_honest_when_nothing_startable(
    tmp_path, monkeypatch
):
    """No target, no detectable serve command: falls through to the existing
    target_url=None path unchanged — never silently invents a URL."""
    monkeypatch.setattr("agents.evaluator._nix_verify_mode", lambda spec_dir: False)
    monkeypatch.setattr("agents.evaluator.detect_serve_command", lambda *a, **k: None)
    st = {"id": "x", "lane": "api"}
    result = _build_kube_or_static_bundle(
        tmp_path,
        tmp_path,
        st,
        make_runner=lambda url: url,
        make_bundle=lambda runner: f"bundle[{runner}]",
    )
    assert result == "bundle[None]"
