"""Tests for build → deploy → test orchestration (#233, epic #232).

Covers the schema (BuildStep + DockerRunTarget), the build runner, and the
DockerRunRuntime lifecycle. All subprocess/network behind seams — no Docker.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tfactory_yml.schema import BuildStep, DockerRunTarget, TFactoryConfig
from tools.runners.build_runner import run_build_steps
from tools.runners.docker_run_runtime import DockerRunRuntime, DockerRunRuntimeError

# ─── schema ──────────────────────────────────────────────────────────────


def test_docker_run_target_parses():
    t = DockerRunTarget(
        type="docker_run",
        name="api",
        image="myapp:test",
        ports=["3000:3000"],
        wait_for=[{"url": "http://localhost:3000/health", "timeout_seconds": 30}],
    )
    assert t.image == "myapp:test"
    assert t.wait_for[0].url.endswith("/health")


def test_build_step_docker_requires_image():
    with pytest.raises(ValueError, match="requires 'image'"):
        BuildStep(type="docker", dockerfile="Dockerfile")


def test_build_step_command_requires_command():
    with pytest.raises(ValueError, match="requires 'command'"):
        BuildStep(type="command")


def test_config_with_build_and_docker_run():
    cfg = TFactoryConfig(
        version=1,
        build=[{"type": "command", "command": "npm run build"}],
        targets=[{"type": "docker_run", "name": "api", "image": "myapp:test"}],
    )
    assert cfg.build[0].command == "npm run build"
    assert cfg.targets[0].type == "docker_run"


# ─── build runner ────────────────────────────────────────────────────────


class _Runner:
    def __init__(self, codes=None):
        self.calls = []
        self._codes = list(codes or [])

    def __call__(self, argv, *, cwd, capture_output, text):
        self.calls.append((list(argv), cwd))
        rc = self._codes.pop(0) if self._codes else 0
        return SimpleNamespace(returncode=rc, stdout="", stderr="boom" if rc else "")


def test_build_command_step_uses_shell(tmp_path):
    r = _Runner()
    steps = [SimpleNamespace(type="command", command="npm ci && npm run build", cwd=None)]
    res = run_build_steps(steps, repo_root=tmp_path, runner_fn=r)
    assert res.ok
    argv, cwd = r.calls[0]
    assert argv[:2] == ["sh", "-c"]
    assert "npm run build" in argv[2]
    assert cwd == str(tmp_path)


def test_build_docker_step_argv(tmp_path):
    r = _Runner()
    steps = [SimpleNamespace(type="docker", dockerfile="Dockerfile", context=".", image="myapp:test")]
    res = run_build_steps(steps, repo_root=tmp_path, runner_fn=r)
    assert res.ok
    argv, _ = r.calls[0]
    assert argv[:4] == ["docker", "build", "-t", "myapp:test"]
    assert "-f" in argv


def test_build_stops_on_first_failure(tmp_path):
    r = _Runner(codes=[1, 0])
    steps = [
        SimpleNamespace(type="command", command="false", cwd=None),
        SimpleNamespace(type="command", command="echo never", cwd=None),
    ]
    res = run_build_steps(steps, repo_root=tmp_path, runner_fn=r)
    assert res.ok is False
    assert "exit 1" in res.error
    assert len(r.calls) == 1  # second step never ran


def test_build_unknown_type(tmp_path):
    res = run_build_steps([SimpleNamespace(type="wat")], repo_root=tmp_path, runner_fn=_Runner())
    assert res.ok is False
    assert "unknown build step type" in res.error


def test_build_empty_steps_ok(tmp_path):
    assert run_build_steps([], repo_root=tmp_path, runner_fn=_Runner()).ok is True


# ─── DockerRunRuntime ────────────────────────────────────────────────────


class _DockerRunner:
    def __init__(self, run_code=0):
        self.calls = []
        self._run_code = run_code

    def __call__(self, argv, *, capture_output, text):
        self.calls.append(list(argv))
        rc = 0
        out = ""
        if argv[:2] == ["docker", "run"]:
            rc = self._run_code
            out = "container123"
        return SimpleNamespace(returncode=rc, stdout=out, stderr="err" if rc else "")


def _target(**kw):
    base = {
        "name": "api",
        "image": "myapp:test",
        "ports": ["3000:3000"],
        "env": {"FOO": "bar"},
        "command": None,
        "wait_for": [],
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_runtime_start_argv_and_target_url():
    r = _DockerRunner()
    wf = [SimpleNamespace(url="http://localhost:3000/health", expect_status=200, timeout_seconds=30)]
    # Pin the allocated host port so the assertions are deterministic; the
    # declared container port (3000) is bound to this dynamic host port
    # (RFC-0016 #465), and target_url is rewritten to it.
    rt = DockerRunRuntime(
        _target(wait_for=wf), runner_fn=r, clock=lambda: 0.0, port_picker=lambda: 54999
    )
    res = rt.start()
    assert res.started and res.container_id == "container123"
    run_argv = next(a for a in r.calls if a[:2] == ["docker", "run"])
    assert "-d" in run_argv
    # dynamic host port -> declared container port, NOT the fixed 3000:3000
    assert "-p" in run_argv and "54999:3000" in run_argv
    assert "3000:3000" not in run_argv
    assert "FOO=bar" in run_argv
    assert run_argv[-1] == "myapp:test"
    assert rt.target_url == "http://localhost:54999/health"


def test_runtime_start_failure_raises():
    rt = DockerRunRuntime(_target(), runner_fn=_DockerRunner(run_code=1), clock=lambda: 0.0)
    with pytest.raises(DockerRunRuntimeError, match="docker run failed"):
        rt.start()


def test_runtime_stop_removes_container():
    r = _DockerRunner()
    # Pin the unique container name so the rm assertion is deterministic; in
    # production the name carries a per-run uuid suffix (RFC-0016 #465).
    rt = DockerRunRuntime(
        _target(), name="tfactory-run-api-deadbeef", runner_fn=r, clock=lambda: 0.0
    )
    rt.start()
    rt.stop()
    assert ["docker", "rm", "-f", "tfactory-run-api-deadbeef"] in r.calls


def test_runtime_context_manager_tears_down():
    r = _DockerRunner()
    with DockerRunRuntime(_target(), runner_fn=r, clock=lambda: 0.0):
        pass
    assert any(a[:3] == ["docker", "rm", "-f"] for a in r.calls)


def test_runtime_command_override_appended():
    r = _DockerRunner()
    rt = DockerRunRuntime(_target(command=["./serve", "--port", "3000"]), runner_fn=r, clock=lambda: 0.0)
    rt.start()
    run_argv = next(a for a in r.calls if a[:2] == ["docker", "run"])
    assert run_argv[-3:] == ["./serve", "--port", "3000"]


def test_runtime_wait_for_healthy_empty_passes():
    rt = DockerRunRuntime(_target(wait_for=[]), runner_fn=_DockerRunner(), clock=lambda: 0.0)
    rt.start()
    rt.wait_for_healthy()  # no URLs → trivially healthy


def test_runtime_wait_for_healthy_success(monkeypatch):
    import tools.runners.docker_run_runtime as mod

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urlrequest, "urlopen", lambda *a, **k: _Resp())
    wf = [SimpleNamespace(url="http://x/health", expect_status=200, timeout_seconds=5)]
    rt = DockerRunRuntime(_target(wait_for=wf), runner_fn=_DockerRunner(), clock=lambda: 0.0)
    rt.start()
    rt.wait_for_healthy()  # urlopen returns 200 → healthy


def test_runtime_wait_for_healthy_timeout(monkeypatch):
    import tools.runners.docker_run_runtime as mod

    def _boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(mod.urlrequest, "urlopen", _boom)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    clk = iter([0.0, 1.0, 2.0, 99.0])
    wf = [SimpleNamespace(url="http://x/health", expect_status=200, timeout_seconds=5)]
    rt = DockerRunRuntime(_target(wait_for=wf), runner_fn=_DockerRunner(), clock=lambda: next(clk))
    rt.start()
    with pytest.raises(DockerRunRuntimeError, match="not healthy"):
        rt.wait_for_healthy()
