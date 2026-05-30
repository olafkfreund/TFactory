"""
CredentialBroker — the agent-facing entry point for the secrets layer.

Responsibilities:
  - ``resolve_ref(ref)`` — resolve any secret reference through the backend
    factory, gated by egress for non-LOCAL backends.
  - ``resolve_cloud(provider)`` — obtain credentials for a cloud provider
    (``gcp`` / ``aws`` / ``azure`` / ``kubernetes``). First tries a
    backend-configured ref for that provider (the "fetch from a vault" head),
    then falls back to the existing ``core.mcp_credentials`` ambient chain.
  - Ephemeral **materialisation**: secret values that must be files (kubeconfig,
    GCP ADC JSON) are written to a per-task scratch dir at mode 0600 and wiped
    on ``close()`` / process exit. Resolved env vars are accumulated for the
    agent environment.

Egress posture (design decision D4): cloud resolution is **off by default**.
The broker resolves cloud creds only when ``egress_allowed=True`` (wired to the
project's ``.tfactory.yml`` ``egress.enabled`` in issue #8). Local backends
(``env`` / ``localfile``) never egress and are always allowed.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import stat
import tempfile
import weakref
from functools import lru_cache
from pathlib import Path

from tfactory_secrets import (
    EgressClass,
    SecretsError,
    SecretValue,
)

logger = logging.getLogger(__name__)

#: Operator-level mapping of cloud providers → backend refs. Separate from
#: ~/.tfactory/mcp-credentials.json (which maps ambient sources); issue #9
#: formalises the schema + a per-project .tfactory.yml block.
CREDENTIALS_CONFIG_PATH = Path.home() / ".tfactory" / "credentials.json"

_CLOUD_PROVIDERS = ("gcp", "aws", "azure", "kubernetes")


class CredentialBroker:
    """Resolve + materialise credentials for a single task.

    Use as a context manager (``with CredentialBroker(...) as b:``) or call
    ``close()`` to wipe materialised secret files.
    """

    def __init__(
        self,
        project_dir: Path | str | None = None,
        spec_dir: Path | str | None = None,
        *,
        egress_allowed: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self.spec_dir = Path(spec_dir).resolve() if spec_dir else None
        self.egress_allowed = egress_allowed
        self._scratch: Path | None = None
        self._materialised: list[Path] = []
        self._env: dict[str, str] = {}
        self._closed = False
        # Wipe materialised files even if close() is never called.
        self._finalizer = weakref.finalize(self, _wipe_paths, self._materialised)
        atexit.register(self.close)

    # -- public API ---------------------------------------------------------

    def resolve_ref(self, ref: str) -> SecretValue:
        """Resolve a single secret reference. Non-LOCAL backends require egress."""
        from tfactory_secrets.factory import get_secrets_backend
        from tfactory_secrets.refs import parse_ref

        parsed = parse_ref(ref)
        backend = get_secrets_backend(parsed.backend)
        if backend.egress_class() is not EgressClass.LOCAL and not self.egress_allowed:
            raise SecretsError(
                f"Refusing to resolve {ref!r}: backend {parsed.backend!r} egresses "
                f"({backend.egress_class().value}) and egress is not enabled for this "
                "task (set egress.enabled in .tfactory.yml)."
            )
        return backend.resolve(parsed)

    def resolve_cloud(self, provider: str):
        """Resolve credentials for a cloud provider → ``CredentialStatus``.

        Returns an unavailable status when egress is disabled. With egress on,
        tries a backend-configured ref first, then the ambient
        ``core.mcp_credentials`` chain.
        """
        from core.mcp_credentials import CredentialStatus, get_credential_status

        if provider not in _CLOUD_PROVIDERS:
            return CredentialStatus(False, f"unknown-provider:{provider}")
        if not self.egress_allowed:
            return CredentialStatus(False, "egress-disabled")

        # 1. Backend-fetch head: an operator-configured ref for this provider.
        entry = _cloud_config().get(provider)
        if entry and entry.get("ref"):
            try:
                status = self._materialise_cloud_entry(provider, entry)
                if status is not None:
                    return status
            except (SecretsError, NotImplementedError, OSError) as exc:
                logger.warning(
                    "CredentialBroker: backend ref for %s failed (%s); "
                    "falling back to ambient credentials.",
                    provider, exc,
                )

        # 2. Fall back to the existing ambient resolution chain.
        return get_credential_status(provider)

    def materialised_env(self) -> dict[str, str]:
        """The env vars resolved/materialised so far (copy)."""
        return dict(self._env)

    def apply_to_env(self, env: dict[str, str]) -> dict[str, str]:
        """Merge materialised env vars into ``env`` (broker values win)."""
        env.update(self._env)
        return env

    def close(self) -> None:
        """Wipe all materialised secret files."""
        if self._closed:
            return
        self._closed = True
        _wipe_paths(self._materialised)
        self._materialised.clear()
        self._env.clear()
        if self._scratch and self._scratch.exists():
            shutil.rmtree(self._scratch, ignore_errors=True)

    def __enter__(self) -> CredentialBroker:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _materialise_cloud_entry(self, provider: str, entry: dict):
        """Resolve ``entry['ref']`` and materialise it per ``entry['kind']``."""
        from core.mcp_credentials import CredentialStatus

        secret = self.resolve_ref(entry["ref"])
        env_name = entry.get("as")
        kind = entry.get("kind", "env")
        if not env_name:
            return None

        if kind == "file":
            path = self.materialise_file(f"{provider}-cred", secret.value)
            self._env[env_name] = str(path)
            source = f"{secret.source}->file:{path.name}"
        else:
            self._env[env_name] = secret.value
            source = secret.source
        return CredentialStatus(
            available=True, source=f"broker:{source}", env_vars=dict(self._env)
        )

    def materialise_file(self, name: str, content: str, mode: int = 0o600) -> Path:
        """Write ``content`` to a 0600 file in the per-task scratch dir; tracked
        for wipe on close."""
        scratch = self._ensure_scratch()
        path = scratch / name
        # Create exclusively, then write — never leave a world-readable window.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, mode)
        self._materialised.append(path)
        return path

    def _ensure_scratch(self) -> Path:
        if self._scratch is not None:
            return self._scratch
        if self.spec_dir is not None:
            base = self.spec_dir / ".tfactory-credentials"
            base.mkdir(parents=True, exist_ok=True)
        else:
            base = Path(tempfile.mkdtemp(prefix="tfactory-cred-"))
        os.chmod(base, stat.S_IRWXU)  # 0700
        self._scratch = base
        # Keep the finalizer tracking the same list object we append to.
        self._finalizer = weakref.finalize(self, _wipe_paths, self._materialised)
        return base


def inject_task_credentials(
    env: dict[str, str],
    project_dir: Path | str | None = None,
    spec_dir: Path | str | None = None,
) -> dict[str, str]:
    """Best-effort: merge broker-resolved cloud creds into ``env`` for a task.

    **Off by default** and fully fault-tolerant — it must never break agent
    creation. Credential resolution only happens when egress is explicitly
    enabled. The per-project ``.tfactory.yml`` ``egress.enabled`` gate is wired
    in issue #8; until then this honours the ``TFACTORY_EGRESS_ENABLED`` env
    flag, so the default path creates no broker and does no work.

    Materialised cred files are wiped at process exit; the agent subprocess
    inherits ``env`` (and reads any cred files it points to) during the run.
    """
    if os.environ.get("TFACTORY_EGRESS_ENABLED", "").strip().lower() not in ("1", "true"):
        return env
    try:
        broker = CredentialBroker(project_dir, spec_dir, egress_allowed=True)
        for provider in _CLOUD_PROVIDERS:
            broker.resolve_cloud(provider)  # materialises env + files (if configured)
        broker.apply_to_env(env)
    except Exception as exc:  # noqa: BLE001 - never break the agent on creds
        logger.warning("CredentialBroker: credential injection skipped: %s", exc)
    return env


def _wipe_paths(paths: list[Path]) -> None:
    for p in list(paths):
        try:
            if Path(p).exists():
                Path(p).unlink()
        except OSError:  # pragma: no cover - best effort
            pass


@lru_cache(maxsize=1)
def _cloud_config() -> dict:
    """Read ~/.tfactory/credentials.json -> {provider: {ref, as, kind}}."""
    if not CREDENTIALS_CONFIG_PATH.exists():
        return {}
    try:
        mode = CREDENTIALS_CONFIG_PATH.stat().st_mode & 0o777
        if mode & 0o077:
            logger.warning(
                "%s is group/world-accessible (mode %o); recommend chmod 600.",
                CREDENTIALS_CONFIG_PATH, mode,
            )
        data = json.loads(CREDENTIALS_CONFIG_PATH.read_text(encoding="utf-8"))
        return data.get("cloud", {}) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read %s: %s", CREDENTIALS_CONFIG_PATH, exc)
        return {}


def reset_config_cache() -> None:
    """Drop the cached credentials config (test/CLI helper)."""
    _cloud_config.cache_clear()


__all__ = [
    "CredentialBroker",
    "inject_task_credentials",
    "reset_config_cache",
    "CREDENTIALS_CONFIG_PATH",
]
