"""Adapt :class:`DockerRunner` to the unified ``ExecutionSandbox`` seam (#426).

``DockerRunner.run`` takes an explicit ``(repo_path, scratch_path, command,
timeout_sec, …)`` keyword surface; the Nix-Job engine's ``run`` takes
``(commands, *, workdir, timeout)``. This thin adapter binds a ``DockerRunner``
to a fixed ``(repo_path, scratch_path)`` workspace and presents the same
``run(commands, *, workdir, timeout)`` surface the seam defines, so both engines
are interchangeable behind ``agents.execution_sandbox.ExecutionSandbox``.

The adapter holds no extra behaviour — it only re-shapes arguments and forwards
the call. The returned ``DockerRunResult`` already satisfies ``RunResultLike``,
so the result type matches the seam too.
"""

from __future__ import annotations

from pathlib import Path

from tools.runners.docker_runner import DockerRunner, DockerRunResult


class DockerExecutionSandbox:
    """Present a :class:`DockerRunner` through the ``ExecutionSandbox`` seam.

    Args:
        runner: The configured ``DockerRunner`` (image, limits, network) to
            delegate to.
        repo_path: Absolute host path of the project worktree, mounted
            read-only at ``/work``. Used when ``run`` is called without an
            explicit ``workdir``.
        scratch_path: Absolute host path of the writable scratch volume,
            mounted at ``/scratch`` (where junit/coverage artifacts land).
    """

    def __init__(
        self,
        runner: DockerRunner,
        *,
        repo_path: Path,
        scratch_path: Path,
    ) -> None:
        self._runner = runner
        self._repo_path = repo_path
        self._scratch_path = scratch_path

    def run(
        self,
        commands: list[str],
        *,
        workdir: str | None = None,
        timeout: int = 600,
    ) -> DockerRunResult:
        """Run ``commands`` in the bound Docker workspace via ``DockerRunner``.

        ``workdir``, when given, overrides the bound ``repo_path`` (a lane may
        point at a sub-worktree); otherwise the bound ``repo_path`` is used.
        """
        repo = Path(workdir) if workdir is not None else self._repo_path
        return self._runner.run(
            repo_path=repo,
            scratch_path=self._scratch_path,
            command=commands,
            timeout_sec=timeout,
        )
