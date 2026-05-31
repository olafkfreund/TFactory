"""Sandbox credential injection for network-enabled lanes (#73).

Per-lane gating: the **unit** lane runs ``--network=none`` with NO credentials.
Only **network-enabled** lanes (api / integration, ``network != "none"``)
receive broker-resolved credentials, and only when **egress is explicitly
opted in** for the project. Resolved secret files (e.g. a kubeconfig) are
materialised 0600 on the host by the broker, bind-mounted **read-only** into
the container, and **wiped after the run** via :meth:`SandboxCredentials.wipe`.

This keeps the default path (unit lane, egress off) completely credential-free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where a materialised kubeconfig is mounted inside the container.
_CONTAINER_KUBECONFIG = "/root/.kube/config"

#: Cloud providers the broker may resolve env credentials for.
_CLOUD_PROVIDERS = ("gcp", "aws", "azure", "kubernetes")


@dataclass
class SandboxCredentials:
    """Resolved sandbox credentials: env vars + read-only file mounts.

    ``files`` maps host path → container path (passed to ``DockerRunner.run``'s
    ``secret_files``). ``broker`` is retained so :meth:`wipe` can erase the
    materialised host files once the container has exited.
    """

    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    broker: object | None = None

    def wipe(self) -> None:
        """Erase materialised secret files (best-effort; never raises)."""
        broker = self.broker
        self.broker = None
        if broker is not None and hasattr(broker, "close"):
            try:
                broker.close()
            except Exception:  # noqa: BLE001 - cleanup must never raise
                logger.warning("sandbox credential wipe failed", exc_info=True)


def resolve_sandbox_credentials(
    project_dir: Path | str | None,
    spec_dir: Path | str | None,
    network: str | None,
) -> SandboxCredentials:
    """Resolve sandbox credentials, gated by lane (network) + egress opt-in.

    Returns **empty** credentials (the unit-lane case) when the lane is
    hermetic (``network`` in ``{None, "", "none"}``) or when egress is not
    enabled for the project. Otherwise resolves cloud creds via the
    CredentialBroker: cloud tokens as env vars, and a kubeconfig (when the
    broker materialises one) mounted read-only at ``/root/.kube/config``.

    Fully fault-tolerant: any resolution failure yields empty creds rather than
    breaking the lane.
    """
    # Hermetic lanes (unit) get nothing — no network, no creds.
    if network in (None, "", "none"):
        return SandboxCredentials()

    try:
        from tfactory_secrets.egress import egress_enabled

        if not egress_enabled(project_dir):
            return SandboxCredentials()
        from tfactory_secrets.broker import CredentialBroker

        broker = CredentialBroker(project_dir, spec_dir, egress_allowed=True)
    except Exception:  # noqa: BLE001 - never break the lane on creds
        logger.warning("sandbox credential resolution unavailable", exc_info=True)
        return SandboxCredentials()

    env: dict[str, str] = {}
    files: dict[str, str] = {}
    try:
        for provider in _CLOUD_PROVIDERS:
            status = broker.resolve_cloud(provider)
            if status and getattr(status, "available", False):
                env.update(getattr(status, "env_vars", {}) or {})
        # If the broker materialised a kubeconfig, mount that host file
        # read-only and repoint KUBECONFIG at the in-container path.
        kubeconfig = env.get("KUBECONFIG")
        if kubeconfig and Path(kubeconfig).exists():
            files[str(Path(kubeconfig).resolve())] = _CONTAINER_KUBECONFIG
            env["KUBECONFIG"] = _CONTAINER_KUBECONFIG
    except Exception:  # noqa: BLE001 - degrade to whatever resolved cleanly
        logger.warning("sandbox credential resolution failed", exc_info=True)

    return SandboxCredentials(env=env, files=files, broker=broker)


__all__ = ["SandboxCredentials", "resolve_sandbox_credentials"]
