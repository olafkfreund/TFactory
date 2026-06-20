"""Concurrency-isolation tests for runtime lanes (RFC-0016 #465).

Proves the collision fixes for the app-under-test runtimes without touching a
real docker daemon or k8s cluster — every external call is behind an injectable
seam and we assert the *constructed* commands/args:

  1. Two concurrent DockerRunRuntime instances for the SAME target get distinct
     container names AND distinct host ports (no -p collision).
  2. AppRuntime uses a distinct compose project name per instance, passed via
     ``-p`` to both ``up`` and ``down``.
  3. KubernetesRuntime defaults the local port to 0 (auto-free) so concurrent
     port-forwards don't clash on a fixed local port.
  4. free_port.find_free_port returns a usable, OS-assigned port.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tools.runners.app_runtime import AppRuntime
from tools.runners.docker_run_runtime import DockerRunRuntime
from tools.runners.free_port import find_free_port
from tools.runners.kubernetes_runtime import KubernetesRuntime

# ── helpers ────────────────────────────────────────────────────────────────


class _RecordingRunner:
    """Stand-in for ``subprocess.run`` that records argv and fakes success."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, capture_output, text):  # noqa: ARG002
        self.calls.append(list(argv))
        out = "container123" if argv[:2] == ["docker", "run"] else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    def run_argvs(self) -> list[list[str]]:
        return [a for a in self.calls if a[:2] == ["docker", "run"]]


def _docker_target(**kw):
    base = {
        "name": "api",
        "image": "myapp:test",
        "ports": ["3000:3000"],
        "env": {},
        "command": None,
        "wait_for": [
            SimpleNamespace(
                url="http://localhost:3000/health",
                expect_status=200,
                timeout_seconds=30,
            )
        ],
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _compose_target():
    return SimpleNamespace(
        type="docker_compose",
        name="web",
        compose_file="docker-compose.test.yml",
        services=["app", "db"],
        wait_for=[],
    )


def _k8s_target():
    return SimpleNamespace(
        type="kubernetes",
        name="cluster",
        context="prod-readonly",
        namespace="example-app",
        service="billing",
        port=8080,
        port_forward=True,
    )


def _port_sequence(*ports):
    """A deterministic port_picker yielding *ports* in order."""
    it = iter(ports)
    return lambda: next(it)


# ── 1. DockerRunRuntime: concurrent runs don't collide ───────────────────────


def test_two_docker_runs_get_distinct_container_names():
    rt_a = DockerRunRuntime(_docker_target(), clock=lambda: 0.0)
    rt_b = DockerRunRuntime(_docker_target(), clock=lambda: 0.0)
    assert rt_a.container_name != rt_b.container_name
    assert rt_a.container_name.startswith("tfactory-run-api-")
    assert rt_b.container_name.startswith("tfactory-run-api-")


def test_two_docker_runs_get_distinct_host_ports():
    runner_a = _RecordingRunner()
    runner_b = _RecordingRunner()
    rt_a = DockerRunRuntime(
        _docker_target(), runner_fn=runner_a, clock=lambda: 0.0,
        port_picker=_port_sequence(40001),
    )
    rt_b = DockerRunRuntime(
        _docker_target(), runner_fn=runner_b, clock=lambda: 0.0,
        port_picker=_port_sequence(40002),
    )
    rt_a.start()
    rt_b.start()

    argv_a = runner_a.run_argvs()[0]
    argv_b = runner_b.run_argvs()[0]
    # Each binds the declared container port to its OWN dynamic host port; the
    # fixed 3000:3000 mapping (which would collide) is never emitted.
    assert "40001:3000" in argv_a
    assert "40002:3000" in argv_b
    assert "3000:3000" not in argv_a
    assert "3000:3000" not in argv_b
    # target_url is rewritten to each run's own host port.
    assert rt_a.target_url == "http://localhost:40001/health"
    assert rt_b.target_url == "http://localhost:40002/health"


def test_docker_run_cleanup_targets_unique_name():
    runner = _RecordingRunner()
    rt = DockerRunRuntime(_docker_target(), runner_fn=runner, clock=lambda: 0.0)
    rt.start()
    rt.stop()
    # rm -f must target the SAME unique name used for run --name.
    rm_calls = [a for a in runner.calls if a[1:3] == ["rm", "-f"]]
    assert rm_calls, "expected a docker rm -f call"
    assert all(a[-1] == rt.container_name for a in rm_calls)


def test_docker_run_handles_ports_without_explicit_host():
    """A ``"3000"`` mapping (no host side) still binds a dynamic host port."""
    runner = _RecordingRunner()
    rt = DockerRunRuntime(
        _docker_target(ports=["3000"]), runner_fn=runner, clock=lambda: 0.0,
        port_picker=_port_sequence(45000),
    )
    rt.start()
    assert "45000:3000" in runner.run_argvs()[0]


# ── 2. AppRuntime: distinct compose project names ────────────────────────────


def test_two_app_runtimes_get_distinct_project_names():
    rt_a = AppRuntime(_compose_target(), Path("/repo"))
    rt_b = AppRuntime(_compose_target(), Path("/repo"))
    assert rt_a.project_name != rt_b.project_name
    assert rt_a.project_name.startswith("tf-")
    assert rt_b.project_name.startswith("tf-")


def test_app_runtime_passes_project_name_to_up_and_down():
    runner = _RecordingRunner()
    rt = AppRuntime(
        _compose_target(), Path("/repo"), runner_fn=runner, clock=lambda: 0.0,
        project_name="tf-fixed01",
    )
    rt.start()
    rt.stop()
    assert len(runner.calls) == 2
    up_argv, down_argv = runner.calls
    # Both up and down carry the SAME unique -p project, so down tears down the
    # exact stack up brought up (and concurrent stacks stay isolated).
    assert up_argv[:3] == ["docker", "compose", "-p"]
    assert up_argv[3] == "tf-fixed01"
    assert "up" in up_argv
    assert down_argv[:3] == ["docker", "compose", "-p"]
    assert down_argv[3] == "tf-fixed01"
    assert "down" in down_argv


# ── 3. KubernetesRuntime: default to auto-free port ──────────────────────────


def test_k8s_runtime_defaults_local_port_to_zero():
    rt = KubernetesRuntime(_k8s_target())
    argv = rt.port_forward_argv()
    # local:remote — local side defaults to 0 (kubectl picks a free port).
    assert argv[-1] == "0:8080"


def test_k8s_runtime_explicit_local_port_pins_bind():
    rt = KubernetesRuntime(_k8s_target(), local_port=9000)
    assert rt.port_forward_argv()[-1] == "9000:8080"


# ── 4. free_port helper ──────────────────────────────────────────────────────


def test_find_free_port_returns_usable_port():
    port = find_free_port()
    assert isinstance(port, int)
    assert 1 <= port <= 65535


def test_find_free_port_varies_across_calls():
    # Not strictly guaranteed by the OS, but in practice successive bind(0)
    # calls hand out different ports; this guards against a constant stub.
    ports = {find_free_port() for _ in range(5)}
    assert len(ports) >= 2
