"""ENV-GATED k8s-Job disposable target for VAL-3 effectful verification.

Implements the DisposableTarget protocol (disposable_target.py) as a thin
adapter over KubeJobSandbox — each VAL-3 effectful command runs as an
ephemeral Kubernetes Job (create→watch→logs→delete in one sandbox.run() call).

Registration is env-gated so the default is COMPLETELY UNCHANGED:

  * ``TFACTORY_VAL3_K8S_JOB=1``       — enable the ``k8s-job`` backend; also
                                          makes ``select_backend()`` return
                                          ``"k8s-job"`` for this env (additive).
  * ``TFACTORY_VAL3_K8S_JOB_IMAGE``   — runner image (e.g. the tfactory-runner-nix
                                          image); required at provision time.

When neither variable is set this module has **no side-effects**: importing it
does not change the ``_PROVISIONERS`` registry, ``select_backend()`` still
returns ``None`` for an empty env, and VAL-3 stays honestly ``not_run``.

Activation needs no startup import: ``disposable_target()`` lazily imports this
module and calls :func:`auto_register` when the backend is selected but not yet
registered, so flipping the env vars alone enables the backend.

Typical operator opt-in (Helm values / env injection)::

    TFACTORY_VAL3_K8S_JOB=1
    TFACTORY_VAL3_K8S_JOB_IMAGE=ghcr.io/my-org/tfactory-runner-nix:latest

"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from agents.disposable_target import register_provisioner
from agents.workspace_status import truthy as _truthy

logger = logging.getLogger(__name__)

# Env-var names — kept here as single source of truth so tests can import them.
ENV_ENABLE = "TFACTORY_VAL3_K8S_JOB"
ENV_IMAGE = "TFACTORY_VAL3_K8S_JOB_IMAGE"

_BACKEND = "k8s-job"

__all__ = [
    "ENV_ENABLE",
    "ENV_IMAGE",
    "K8sJobTarget",
    "auto_register",
]


class K8sJobTarget:
    """VAL-3 disposable target — runs effectful commands as ephemeral k8s Jobs.

    A thin adapter over :class:`~tools.runners.kube_sandbox.KubeJobSandbox`.
    Each :meth:`run` call dispatches a single ephemeral Job; the Job's own
    ``finally`` block deletes it, so :meth:`teardown` is a logged no-op (the
    guarantee is already provided by the sandbox). Idempotent: safe to call
    :meth:`teardown` more than once.

    Implements the :class:`~agents.disposable_target.DisposableTarget` protocol
    exactly — same ``.name``, ``.run(command, *, timeout)``, ``.teardown()``.
    """

    def __init__(self, sandbox: Any, *, name: str | None = None) -> None:
        """
        Args:
            sandbox: A :class:`~tools.runners.kube_sandbox.KubeJobSandbox` (or
                     any object with a compatible ``.run(commands, *, timeout)``
                     method returning a result with ``.ok: bool`` and
                     ``.output: str``).  Injected for testability.
            name:    Human-readable target identifier.  Defaults to a short UUID.
        """
        self._sandbox = sandbox
        self.name: str = name or f"k8s-job-{uuid.uuid4().hex[:8]}"
        self._torn_down: bool = False

    # ------------------------------------------------------------------
    # DisposableTarget protocol
    # ------------------------------------------------------------------

    def run(self, command: str, *, timeout: float = 600.0) -> tuple[bool, str]:
        """Run one effectful command as an ephemeral k8s Job.

        Args:
            command: Shell command to execute inside the runner image.
            timeout: Seconds to wait for the Job to complete (default 600).

        Returns:
            ``(ok, output)`` — same contract as
            :class:`~agents.disposable_target.DisposableTarget`.
        """
        # Clamp: int() floors, and a 0/negative activeDeadlineSeconds downstream
        # would kill the Job immediately.
        result = self._sandbox.run([command], timeout=max(1, int(timeout)))
        return result.ok, result.output

    def teardown(self) -> None:
        """Idempotent teardown — a no-op because each Job auto-deletes itself.

        The KubeJobSandbox ``_run_async`` method deletes the Job in its own
        ``finally`` block, so there is nothing left to destroy here.  Logging
        on the first call, silent on subsequent calls.
        """
        if not self._torn_down:
            logger.debug(
                "K8sJobTarget %s: teardown called (each Job already auto-deleted)",
                self.name,
            )
            self._torn_down = True


# ---------------------------------------------------------------------------
# Provisioner factory and startup auto-registration
# ---------------------------------------------------------------------------


def _make_provisioner(
    env: Mapping[str, str] | None = None,
) -> Callable[[dict[str, Any]], K8sJobTarget | None]:
    """Return a provisioner factory for the ``k8s-job`` backend.

    The factory is called by :func:`~agents.disposable_target.disposable_target`
    at VAL-3 provisioning time.  It reads ``TFACTORY_VAL3_K8S_JOB_IMAGE`` at
    provision time (not at registration time), so the image is never captured
    stale during :func:`auto_register`.  Returns ``None`` when the image is
    unset (honest not_run rather than a fake pass).
    """
    _env = os.environ if env is None else env

    def _provision(_spec: dict[str, Any]) -> K8sJobTarget | None:
        image = (_env.get(ENV_IMAGE) or "").strip()
        if not image:
            logger.warning(
                "K8sJobTarget: %s is unset; cannot provision k8s-job target",
                ENV_IMAGE,
            )
            return None
        from tools.runners.kube_sandbox import KubeJobSandbox  # noqa: PLC0415 - lazy

        sandbox = KubeJobSandbox(image)
        return K8sJobTarget(sandbox)

    return _provision


def auto_register(env: Mapping[str, str] | None = None) -> bool:
    """Register the k8s-job provisioner iff ``TFACTORY_VAL3_K8S_JOB=1``.

    Completely inert when the env var is unset — safe to call at module import.
    Accepts an explicit ``env`` dict so tests can drive it without touching
    ``os.environ``.

    Returns:
        ``True`` if the provisioner was registered; ``False`` if skipped
        (env var absent or falsy).
    """
    _env = os.environ if env is None else env
    if not _truthy(_env.get(ENV_ENABLE)):
        return False
    # Pass the original ``env`` argument (None means "use os.environ at provision
    # time") so _make_provisioner's own default-handling keeps the type clean.
    register_provisioner(_BACKEND, _make_provisioner(env))
    logger.info(
        "K8sJobTarget: registered k8s-job provisioner (image=%s)",
        _env.get(ENV_IMAGE, "<unset>"),
    )
    return True


# Module-level auto-registration: complete no-op when TFACTORY_VAL3_K8S_JOB
# is unset (the default).  When enabled, the provisioner is wired into the
# _PROVISIONERS registry for the "k8s-job" backend so that select_backend()
# + disposable_target() find it transparently.
auto_register()
