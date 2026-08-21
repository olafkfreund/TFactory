"""A selected KMS backend that cannot construct must not boot (AIFactory#1290).

The chart now refuses to render a selected backend with no key -- but a render
check cannot see an empty Secret, a values file setting the key to ``""``, or a
KMS the pod cannot reach. ``enforce_kms_safety`` is the runtime half.

Deliberately CONSTRUCT-only, with no network round-trip: it catches
configuration faults, which are permanent, without turning a transient KMS
outage during a rolling restart into a CrashLoopBackOff.
"""

from __future__ import annotations

import importlib

import pytest

from tests.secrets.helpers import WEB_SERVER_ROOT  # noqa: F401 — joins sys.path

_kms = importlib.import_module("server.crypto.kms")


@pytest.mark.secrets
def test_selected_backend_that_cannot_construct_fails_at_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cloud backend selected with nothing it needs must refuse to start."""
    monkeypatch.setenv("KMS_BACKEND", "vault_transit")
    monkeypatch.delenv("APP_KMS_BACKEND", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    _kms.reset_backend_cache()

    with pytest.raises(SystemExit) as excinfo:
        _kms.enforce_kms_safety()
    assert "Refusing to start" in str(excinfo.value)
    _kms.reset_backend_cache()


@pytest.mark.secrets
def test_the_unconfigured_default_still_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-KMS default posture must NOT be turned into a boot failure."""
    monkeypatch.delenv("KMS_FERNET_KEY", raising=False)
    monkeypatch.delenv("APP_KMS_FERNET_KEY", raising=False)
    monkeypatch.delenv("APP_KMS_BACKEND", raising=False)
    monkeypatch.setenv("KMS_BACKEND", "fernet")
    _kms.reset_backend_cache()

    _kms.enforce_kms_safety()  # must not raise
    assert _kms.encryption_is_required() is False
    _kms.reset_backend_cache()
