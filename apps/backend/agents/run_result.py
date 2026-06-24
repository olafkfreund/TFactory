"""Shared run-result contract for the test-pipeline execution engines.

This is the single structural type that every execution backend's result must
satisfy so downstream primitives (stability re-runs, mutation probes, coverage)
can consume any backend uniformly. It is deliberately dependency-free — only
``typing.Protocol`` — so the agent modules that depend on it stay decoupled from
the concrete runner implementations (``tools.runners.docker_runner`` and the
Nix-Job ``tools.runners.kube_sandbox``) and from that package's eager imports.

Previously this Protocol was copy-pasted, byte-for-byte, into ``evaluator.py``,
``stability_runner.py`` and ``mutate_probe.py`` (each with its own
circular-import-safety note). Centralising it here is the first foundational
step of the execution-engine unification (issue #426): it is the one result
shape the future single ``factory-sandbox`` interface will return.

``DockerRunResult`` (tools.runners.docker_runner) satisfies this structurally.
The Nix-Job ``JobRunResult`` does not yet (it exposes ``exit_code``/``output``);
making it conform is the next increment.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RunResultLike(Protocol):
    """Structural type for a sandboxed test execution result.

    The minimal surface the stability / mutation / coverage primitives read off
    a runner result. ``@runtime_checkable`` so conformance can be asserted with
    a structural ``isinstance`` check in tests.
    """

    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...
