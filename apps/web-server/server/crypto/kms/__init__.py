"""KMS backend abstraction for TFactory's at-rest encryption.

The active backend is selected by the ``APP_KMS_BACKEND`` env var
(or just ``KMS_BACKEND`` outside the pydantic-settings prefix). Each
backend implements the minimal ``encrypt(bytes) -> bytes`` /
``decrypt(bytes) -> bytes`` protocol — the data layer (EncryptedString)
neither knows nor cares about the underlying key-management technology.

P2.1 ships only the ``fernet`` backend (local-key, for dev + tests).
P2.4 adds aws_kms, azure_kv, gcp_kms, vault_transit.
"""

from __future__ import annotations

import os
from typing import Protocol


class Backend(Protocol):
    """Minimum protocol every KMS backend implements."""

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt + authenticate. Result is an opaque blob — backends are
        free to prepend nonces/headers; the data layer treats it as bytes."""
        ...

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Reverse encrypt(). Raises ``InvalidTag`` (or backend-specific
        equivalent) when the blob has been tampered with."""
        ...


_INSTANCE: Backend | None = None

# The default backend. Selecting anything else is an explicit operator
# statement that credentials must be encrypted at rest (AIFactory#1290).
DEFAULT_BACKEND = "fernet"


def configured_backend_name() -> str:
    """The backend name from env, normalised. ``fernet`` when unset."""
    return (
        (
            os.environ.get("APP_KMS_BACKEND")
            or os.environ.get("KMS_BACKEND")
            or DEFAULT_BACKEND
        )
        .strip()
        .lower()
    )


def encryption_is_required() -> bool:
    """True when the operator explicitly chose a non-default KMS backend.

    ``fernet`` (the default, and what you get with no env at all) is the
    single-operator posture where a missing key means nothing was ever
    provisioned. Anything else means the operator asked for a KMS, and a
    deployment that cannot use it is a misconfiguration, not a fallback.
    """
    return configured_backend_name() != DEFAULT_BACKEND


def enforce_kms_safety() -> None:
    """Refuse to start when a selected KMS backend cannot be constructed.

    Called only from the real server entrypoint (``server.main.__main__``), so
    TestClient never reaches it. The chart now refuses to render a selected
    backend with no key (AIFactory#1290) -- but a render check cannot see an
    empty Secret, a values file setting the key to ``""``, or a KMS the pod
    cannot reach, so this catches what the chart cannot.

    Deliberately CONSTRUCT-only: every backend's ``from_env()`` reads
    environment and builds a client, with no network round-trip. So this
    catches configuration faults, which are permanent and cannot heal at
    runtime, and it does NOT turn a transient KMS outage during a rolling
    restart into a CrashLoopBackOff -- that stays a per-write failure through
    ``EncryptedString``, which already raises rather than storing plaintext.
    """
    if not encryption_is_required():
        return
    try:
        get_backend()
    except Exception as exc:  # every backend raises its own error type
        raise SystemExit(
            f"Refusing to start: APP_KMS_BACKEND={configured_backend_name()!r} "
            f"was selected but the backend could not be constructed ({exc}). "
            "Every credential read or write would fail at runtime. Provision "
            "the backend's key/credentials, or unset APP_KMS_BACKEND."
        ) from exc


def get_backend() -> Backend:
    """Resolve the configured backend. Cached per process.

    Env var precedence (first match wins):
      - ``APP_KMS_BACKEND`` (per the pydantic-settings prefix convention)
      - ``KMS_BACKEND`` (the unprefixed form, used by tests)

    Default: ``fernet``.
    """
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    name = configured_backend_name()

    if name == "fernet":
        from .fernet import FernetBackend
        _INSTANCE = FernetBackend.from_env()
        return _INSTANCE

    if name == "aws_kms":
        from .aws import AwsKmsBackend
        _INSTANCE = AwsKmsBackend.from_env()
        return _INSTANCE

    if name == "vault_transit":
        from .vault import VaultTransitBackend
        _INSTANCE = VaultTransitBackend.from_env()
        return _INSTANCE

    if name == "azure_kv":
        from .azure import AzureKeyVaultBackend
        _INSTANCE = AzureKeyVaultBackend.from_env()
        return _INSTANCE

    if name == "gcp_kms":
        from .gcp import GcpKmsBackend
        _INSTANCE = GcpKmsBackend.from_env()
        return _INSTANCE

    raise ValueError(
        f"unknown KMS backend {name!r} — supported: "
        "fernet, aws_kms, vault_transit, azure_kv, gcp_kms"
    )


def reset_backend_cache() -> None:
    """Test hook: force the next ``get_backend()`` call to re-read env."""
    global _INSTANCE
    _INSTANCE = None
