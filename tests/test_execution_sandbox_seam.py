"""Guard the unified execution-sandbox seam (agents/execution_sandbox.py, #426).

Both test-execution engines must be interchangeable behind one Protocol:

  - The Nix-Job ``KubeJobSandbox`` conforms with no churn (its ``run`` already
    has the seam's shape).
  - ``DockerExecutionSandbox`` adapts the keyword-only ``DockerRunner.run`` to
    the seam, and delegates with the arguments re-shaped correctly.

Constructors here are inert (they store config; no cluster / no container), so
these run without a live backend.
"""

from __future__ import annotations

from pathlib import Path

from agents.execution_sandbox import ExecutionSandbox
from tools.runners.docker_runner import DockerRunner, DockerRunResult
from tools.runners.docker_sandbox import DockerExecutionSandbox
from tools.runners.kube_sandbox import KubeJobSandbox


def test_kube_job_sandbox_conforms_to_seam():
    # Static + runtime conformance: the binding type-checks (mypy, ratchet gate)
    # and the runtime structural check passes.
    sandbox: ExecutionSandbox = KubeJobSandbox("tfactory-runner-nix:latest")
    assert isinstance(sandbox, ExecutionSandbox)


def test_docker_execution_sandbox_conforms_to_seam():
    adapter: ExecutionSandbox = DockerExecutionSandbox(
        DockerRunner(),
        repo_path=Path("/work/repo"),
        scratch_path=Path("/work/scratch"),
    )
    assert isinstance(adapter, ExecutionSandbox)


def test_docker_adapter_delegates_with_reshaped_args(monkeypatch):
    runner = DockerRunner()
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return DockerRunResult(returncode=0, stdout="ok")

    monkeypatch.setattr(runner, "run", fake_run)
    adapter = DockerExecutionSandbox(
        runner, repo_path=Path("/work/repo"), scratch_path=Path("/work/scratch")
    )

    result = adapter.run(["pytest", "-q"], timeout=123)

    assert result.returncode == 0
    assert captured["repo_path"] == Path("/work/repo")
    assert captured["scratch_path"] == Path("/work/scratch")
    assert captured["command"] == ["pytest", "-q"]
    assert captured["timeout_sec"] == 123


def test_docker_adapter_workdir_overrides_bound_repo(monkeypatch):
    runner = DockerRunner()
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return DockerRunResult(returncode=0)

    monkeypatch.setattr(runner, "run", fake_run)
    adapter = DockerExecutionSandbox(
        runner, repo_path=Path("/work/repo"), scratch_path=Path("/work/scratch")
    )

    adapter.run(["pytest"], workdir="/work/other")

    assert captured["repo_path"] == Path("/work/other")
