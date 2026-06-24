"""Unified execution-sandbox seam — issue #426.

TFactory runs a task's test commands through one of two engines:

  * the Kubernetes **Nix-Job** backend (``tools.runners.kube_sandbox.
    KubeJobSandbox``), used where the pod has no container runtime (k3d); and
  * the hardened **Docker/Podman** backend (``tools.runners.docker_runner.
    DockerRunner``), used where a container runtime is present.

Historically each engine had its own call shape. This Protocol is the single
typed seam both present: *run a list of shell commands in a sandboxed workspace
with a timeout, and return a result satisfying* ``RunResultLike``
(``agents/run_result.py``).

``KubeJobSandbox.run`` already matches this signature. ``DockerRunner``'s
richer, keyword-only ``run()`` is adapted to it by
``tools.runners.docker_sandbox.DockerExecutionSandbox``. Introducing the seam
switches **no live caller** — the lanes migrate onto it one at a time in later
increments of #426, after which the unused ``lane_dispatch`` indirection and the
direct-instantiation paths can be retired.

Deliberately dependency-free (only ``typing`` + ``collections.abc`` + the
equally dependency-free ``agents.run_result``) so agent modules can depend on
the seam without importing either concrete engine or that package's eager
imports.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agents.run_result import RunResultLike


@runtime_checkable
class ExecutionSandbox(Protocol):
    """A sandboxed test-execution engine.

    Implementations run ``commands`` in an isolated workspace and return a
    :class:`~agents.run_result.RunResultLike`.

    The signature matches ``KubeJobSandbox.run`` exactly (the de-facto seam the
    Nix-Job engine already exposes), so that engine conforms with no churn;
    ``DockerExecutionSandbox`` adapts ``DockerRunner`` to the same surface.

    Args:
        commands: The shell command(s) to execute in the sandbox.
        workdir: The project worktree to run in (engine-specific meaning); the
            Nix-Job engine co-mounts it from the workspaces PVC, the Docker
            adapter mounts it read-only at ``/work``. ``None`` means the
            engine's default workspace.
        timeout: Hard wall-clock cap, in seconds.
    """

    def run(
        self,
        commands: list[str],
        *,
        workdir: str | None = None,
        timeout: int = 600,
    ) -> RunResultLike: ...
